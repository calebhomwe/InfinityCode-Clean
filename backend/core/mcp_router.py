"""K3 MCP Tool Router — Intelligent tool routing with parallel dispatch and validation.

Based on Kimi K3's tool use capabilities: dynamic registration, parallel execution,
schema validation, and intelligent routing based on tool descriptions and context.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Protocol, Set, TypedDict


class ToolSchema(TypedDict):
    """JSON Schema for tool parameters."""

    type: str
    properties: Dict[str, Any]
    required: List[str]


class ToolDefinition(TypedDict):
    """Definition of an MCP tool."""

    name: str
    description: str
    parameters: ToolSchema


class ToolResult(TypedDict):
    """Result from tool execution."""

    tool: str
    status: str  # "success" | "error" | "timeout"
    output: Any
    execution_time_ms: float
    error: Optional[str]


class ToolRouterMetrics(TypedDict):
    """Metrics for tool routing session."""

    total_calls: int
    successful: int
    failed: int
    timeouts: int
    total_latency_ms: float
    avg_latency_ms: float
    cache_hits: int


class ToolValidator:
    """Lightweight JSON Schema validator for tool parameters."""

    def __init__(self) -> None:
        self._cache: Dict[str, Dict[str, Any]] = {}

    def validate(self, tool_name: str, schema: ToolSchema, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate parameters against schema. Returns (is_valid, error_message)."""
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        # Check required fields
        for field in required:
            if field not in params:
                return False, f"Missing required field: {field}"

        # Type checking for basic types
        for key, value in params.items():
            if key in properties:
                expected_type = properties[key].get("type")
                if expected_type and not self._check_type(value, expected_type):
                    return False, f"Field '{key}' should be {expected_type}, got {type(value).__name__}"

        return True, None

    def _check_type(self, value: Any, expected: str) -> bool:
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected_class = type_map.get(expected)
        if expected_class is None:
            return True
        return isinstance(value, expected_class)


class ToolExecutor(Protocol):
    """Protocol for tool execution functions."""

    async def __call__(self, **params: Any) -> Any:
        ...


@dataclass
class RegisteredTool:
    """Internal representation of a registered tool."""

    name: str
    description: str
    schema: ToolSchema
    executor: ToolExecutor
    category: str = "general"
    cost_estimate: float = 1.0  # Relative cost for routing decisions
    cacheable: bool = False
    timeout_ms: int = 30000


@dataclass
class RoutingDecision:
    """Decision made by the router for a tool call."""

    tool_names: List[str]
    parallel: bool
    reasoning: str
    estimated_cost: float
    confidence: float  # 0.0 - 1.0


class K3McpRouter:
    """K3-inspired MCP Tool Router with intelligent routing and parallel execution.
    
    Features:
    - Dynamic tool registration with schema validation
    - Parallel tool dispatch for independent operations
    - Intelligent routing based on tool descriptions
    - Result caching for cacheable tools
    - Timeout and error handling
    """

    def __init__(self, default_timeout_ms: int = 30000) -> None:
        self._tools: Dict[str, RegisteredTool] = {}
        self._validator = ToolValidator()
        self._cache: Dict[str, Any] = {}
        self._default_timeout_ms = default_timeout_ms
        self._metrics = ToolRouterMetrics(
            total_calls=0,
            successful=0,
            failed=0,
            timeouts=0,
            total_latency_ms=0.0,
            avg_latency_ms=0.0,
            cache_hits=0,
        )

    def register(
        self,
        name: str,
        description: str,
        parameters: ToolSchema,
        executor: ToolExecutor,
        category: str = "general",
        cacheable: bool = False,
        timeout_ms: Optional[int] = None,
        cost_estimate: float = 1.0,
    ) -> "K3McpRouter":
        """Register a new tool with the router.
        
        Args:
            name: Unique tool name
            description: Human-readable description for routing
            parameters: JSON Schema for parameters
            executor: Async function that executes the tool
            category: Tool category for grouping
            cacheable: Whether results can be cached
            timeout_ms: Timeout in milliseconds (default: router default)
            cost_estimate: Relative cost (1.0 = baseline)
        
        Returns:
            Self for chaining
        """
        self._tools[name] = RegisteredTool(
            name=name,
            description=description,
            schema=parameters,
            executor=executor,
            category=category,
            cacheable=cacheable,
            timeout_ms=timeout_ms or self._default_timeout_ms,
            cost_estimate=cost_estimate,
        )
        return self

    def unregister(self, name: str) -> bool:
        """Unregister a tool. Returns True if found and removed."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def list_tools(self, category: Optional[str] = None) -> List[ToolDefinition]:
        """List all registered tools, optionally filtered by category."""
        tools = self._tools.values()
        if category:
            tools = [t for t in tools if t.category == category]
        
        return [
            ToolDefinition(
                name=t.name,
                description=t.description,
                parameters=t.schema,
            )
            for t in tools
        ]

    def get_tool(self, name: str) -> Optional[RegisteredTool]:
        """Get a registered tool by name."""
        return self._tools.get(name)

    async def execute(
        self,
        tool_name: str,
        params: Dict[str, Any],
        timeout_ms: Optional[int] = None,
        use_cache: bool = True,
    ) -> ToolResult:
        """Execute a single tool with validation and timeout.
        
        Args:
            tool_name: Name of the tool to execute
            params: Parameters to pass to the tool
            timeout_ms: Override timeout for this call
            use_cache: Whether to use cached results if available
        
        Returns:
            ToolResult with execution details
        """
        start_time = time.perf_counter()
        self._metrics["total_calls"] += 1

        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(
                tool=tool_name,
                status="error",
                output=None,
                execution_time_ms=0.0,
                error=f"Tool '{tool_name}' not found",
            )

        # Validate parameters
        is_valid, error = self._validator.validate(tool_name, tool.schema, params)
        if not is_valid:
            return ToolResult(
                tool=tool_name,
                status="error",
                output=None,
                execution_time_ms=0.0,
                error=f"Validation failed: {error}",
            )

        # Check cache for cacheable tools
        cache_key = None
        if use_cache and tool.cacheable:
            cache_key = f"{tool_name}:{json.dumps(params, sort_keys=True)}"
            if cache_key in self._cache:
                self._metrics["cache_hits"] += 1
                return ToolResult(
                    tool=tool_name,
                    status="success",
                    output=self._cache[cache_key],
                    execution_time_ms=0.0,
                    error=None,
                )

        # Execute with timeout
        timeout = timeout_ms or tool.timeout_ms
        try:
            result = await asyncio.wait_for(
                tool.executor(**params),
                timeout=timeout / 1000.0,
            )
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._metrics["successful"] += 1
            self._metrics["total_latency_ms"] += elapsed_ms
            self._metrics["avg_latency_ms"] = (
                self._metrics["total_latency_ms"] / self._metrics["successful"]
            )

            # Cache result if applicable
            if cache_key:
                self._cache[cache_key] = result

            return ToolResult(
                tool=tool_name,
                status="success",
                output=result,
                execution_time_ms=elapsed_ms,
                error=None,
            )

        except asyncio.TimeoutError:
            self._metrics["timeouts"] += 1
            return ToolResult(
                tool=tool_name,
                status="timeout",
                output=None,
                execution_time_ms=timeout,
                error=f"Timeout after {timeout}ms",
            )

        except Exception as e:
            self._metrics["failed"] += 1
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return ToolResult(
                tool=tool_name,
                status="error",
                output=None,
                execution_time_ms=elapsed_ms,
                error=str(e),
            )

    async def execute_parallel(
        self,
        calls: List[tuple[str, Dict[str, Any]]],
        timeout_ms: Optional[int] = None,
        use_cache: bool = True,
    ) -> List[ToolResult]:
        """Execute multiple tools in parallel.
        
        Args:
            calls: List of (tool_name, params) tuples
            timeout_ms: Override timeout for all calls
            use_cache: Whether to use cached results
        
        Returns:
            List of ToolResult in the same order as calls
        """
        tasks = [
            self.execute(name, params, timeout_ms, use_cache)
            for name, params in calls
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    def route(self, intent: str, available_tools: Optional[List[str]] = None) -> RoutingDecision:
        """Intelligently route an intent to appropriate tools.
        
        This is a lightweight routing algorithm that matches intent keywords
        against tool descriptions. In production, this would use an LLM.
        
        Args:
            intent: Natural language description of what to do
            available_tools: Optional subset of tools to consider
        
        Returns:
            RoutingDecision with selected tools and execution strategy
        """
        intent_lower = intent.lower()
        words = set(intent_lower.split())
        
        candidates = []
        tool_subset = (
            [self._tools[t] for t in available_tools if t in self._tools]
            if available_tools
            else list(self._tools.values())
        )

        for tool in tool_subset:
            score = 0.0
            desc_lower = tool.description.lower()
            
            # Simple keyword matching
            for word in words:
                if word in desc_lower:
                    score += 0.2
                if word in tool.name.lower():
                    score += 0.3
            
            # Boost for exact category match in intent
            if tool.category in intent_lower:
                score += 0.2

            if score > 0.3:  # Threshold
                candidates.append((tool, score))

        # Sort by score
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        if not candidates:
            return RoutingDecision(
                tool_names=[],
                parallel=False,
                reasoning="No matching tools found",
                estimated_cost=0.0,
                confidence=0.0,
            )

        # Select top tools
        selected = candidates[:3]  # Max 3 tools
        tool_names = [t[0].name for t in selected]
        avg_confidence = sum(s for _, s in selected) / len(selected)
        total_cost = sum(t[0].cost_estimate for t in selected)

        # Decide on parallel execution
        parallel = len(selected) > 1 and all(
            "sequence" not in t[0].description.lower() and
            "chain" not in t[0].description.lower()
            for t in selected
        )

        reasoning = f"Selected {len(selected)} tool(s) based on keyword matching"
        if parallel:
            reasoning += ", parallel execution enabled"

        return RoutingDecision(
            tool_names=tool_names,
            parallel=parallel,
            reasoning=reasoning,
            estimated_cost=total_cost,
            confidence=min(avg_confidence, 1.0),
        )

    async def execute_routed(
        self,
        intent: str,
        params_map: Optional[Dict[str, Dict[str, Any]]] = None,
        timeout_ms: Optional[int] = None,
    ) -> List[ToolResult]:
        """Route intent to tools and execute them.
        
        Args:
            intent: Natural language description of what to do
            params_map: Optional mapping of tool_name -> params
            timeout_ms: Override timeout
        
        Returns:
            List of ToolResult from executed tools
        """
        decision = self.route(intent)
        
        if not decision.tool_names:
            return []

        calls = []
        for name in decision.tool_names:
            params = params_map.get(name, {}) if params_map else {}
            calls.append((name, params))

        if decision.parallel:
            return await self.execute_parallel(calls, timeout_ms)
        else:
            results = []
            for name, params in calls:
                result = await self.execute(name, params, timeout_ms)
                results.append(result)
                # Chain results: subsequent calls get previous output
                if results[-1]["status"] != "success":
                    break
            return results

    def get_metrics(self) -> ToolRouterMetrics:
        """Get current metrics snapshot."""
        return dict(self._metrics)

    def clear_cache(self) -> int:
        """Clear the result cache. Returns number of entries cleared."""
        count = len(self._cache)
        self._cache.clear()
        return count

    def reset_metrics(self) -> None:
        """Reset all metrics to zero."""
        self._metrics.update({
            "total_calls": 0,
            "successful": 0,
            "failed": 0,
            "timeouts": 0,
            "total_latency_ms": 0.0,
            "avg_latency_ms": 0.0,
            "cache_hits": 0,
        })


# Singleton instance for application use
_default_router: Optional[K3McpRouter] = None


def get_router() -> K3McpRouter:
    """Get or create the default router instance."""
    global _default_router
    if _default_router is None:
        _default_router = K3McpRouter()
    return _default_router


def reset_router() -> None:
    """Reset the default router (useful for testing)."""
    global _default_router
    _default_router = None


__all__ = [
    "K3McpRouter",
    "ToolDefinition",
    "ToolResult",
    "ToolSchema",
    "ToolRouterMetrics",
    "RoutingDecision",
    "RegisteredTool",
    "get_router",
    "reset_router",
]