# FABLE + INFINITY CODE Integration Guide

## 🚀 What's Been Set Up

### 1. Model Configuration (`backend/config.yaml`)

Your Infinity Code backend now supports three Fable model lanes:

| Lane | Models | Use Case |
|------|--------|----------|
| `fable` | 27B Fusion → 9B Fast | Balanced intelligence + speed |
| `fable_ultra` | 35B Max → 27B Fusion | Maximum intelligence cascade |
| `fable_fast` | 9B Fast → 27B Fusion | Speed first, 27B backup |

### 2. Local Endpoint (Port 8082)

The config now points to your llama.cpp server running Fable-Fusion-27B:

```yaml
local:
  endpoint: "http://localhost:8082/v1"
  api_key: "dummy"
```

### 3. New Launcher Script

`C:\AI\fable\FABLE-INFINITY.bat` — Double-click to start:
1. llama-server with 27B model
2. Infinity Code backend
3. Infinity Code desktop app

## 🎮 Gaming & 3D Code Datasets

### Game Development Training Data

| Dataset | Source | Size | Description |
|---------|--------|------|-------------|
| **The Stack v2 (Gamedev)** | HuggingFace | 100TB+ | Filter GitHub repos for Unity/Unreal/Godot |
| **GameCodeInstruct** | Make | 500K samples | Game-specific code instructions |
| **RedPajama-Data (Gaming)** | Together AI | 30B tokens | Gaming forum + docs |
| **Unity Manual + Forums** | Scrape | -- | Official docs + community solutions |

**URLs to explore:**
- https://huggingface.co/datasets/bigcode/the-stack-v2
- https://huggingface.co/datasets/LDJnr/GameCodeInstruct
- https://huggingface.co/datasets/Unity-Technologies/ml-agents-datasets
- https://github.com/Unity-Technologies/UnityCsReference

### 3D & Graphics Datasets

| Dataset | Type | Use For |
|---------|------|---------|
| **Blender Python API Docs** | Code + Text | Blender scripting |
| **Three.js Examples** | Code | WebGL/3D web |
| **Shadertoy Collection** | Code | Shader programming |
| **OpenGL/WebGPU Specs** | Code + Docs | Graphics APIs |
| **OBJ/GLTF Model Pairs** | Data | 3D format conversion |

**Key repositories:**
- https://github.com/blender/blender (Blender source)
- https://github.com/mrdoob/three.js (Three.js examples)
- https://github.com/KhronosGroup/glTF (GLTF spec)

### Web Development Datasets

| Dataset | Focus | Samples |
|---------|-------|---------|
| **The Stack (Web)** | HTML/CSS/JS/TS | 500M+ files |
| **React/Vue/Angular Docs** | Framework code | Official + forks |
| **Frontend Masters** | Tutorials → Instructions | Educational |
| **MDN Web Docs** | Reference | Complete web platform |

## 📊 How to Train Your Own Coding Model

### Step 1: Create Training Data

```bash
# Install data tools
pip install datasets huggingface-hub transformers

# Download gaming code dataset
python -c "
from datasets import load_dataset
dataset = load_dataset('bigcode/the-stack-v2', split='train', streaming=True)
# Filter for game engines
game_repos = [r for r in dataset if any(x in r['repo_name'].lower() 
               for x in ['unity', 'unreal', 'godot', 'bevy'])]
"
```

### Step 2: Fine-tune with QLoRA

```python
# Fine-tune your 27B on gaming code
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model

# Load the 27B base
model = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen3.6-27B",
    load_in_4bit=True,
    device_map="auto"
)

# Add LoRA adapters
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)

model = get_peft_model(model, lora_config)

# Train on your gaming dataset
# ... training loop ...

# Save adapters (only ~100MB vs 13GB for full model)
model.save_pretrained("./fabel-gaming-lora")
```

### Step 3: Merge & Quantize

```bash
# Merge LoRA with base model
python merge_lora.py \
  --base_model Qwen/Qwen3.6-27B \
  --lora_model ./fabel-gaming-lora \
  --output ./fabel-gaming-merged

# Quantize to GGUF for llama.cpp
python convert_hf_to_gguf.py \
  --input ./fabel-gaming-merged \
  --output ./fabel-gaming-Q4_K_M.gguf \
  --quantization Q4_K_M
```

## 🔧 Iteration Ideas for Infinity Code

### Feature 1: Game Engine Agent

Create a specialized agent for game development:

```python
# backend/skills/game_dev_agent.py
class GameDevAgent:
    """Specialized agent for Unity/Unreal/Godot development."""
    
    def __init__(self):
        self.engine_context = {
            "unity": load_docs("unity_manual"),
            "unreal": load_docs("ue5_api"),
            "godot": load_docs("godot_api"),
        }
    
    async def generate_component(self, description: str, engine: str):
        """Generate a game component with engine-specific best practices."""
        prompt = f"""
        Engine: {engine}
        Context: {self.engine_context[engine]}
        Task: Create {description}
        
        Output the complete, production-ready code.
        """
        return await self.llm.generate(prompt)
```

### Feature 2: 3D Scene Parser

Add vision capabilities for 3D assets:

```typescript
// src/components/Scene3DViewer.tsx
export function Scene3DViewer({ gltfPath }: { gltfPath: string }) {
  const { scene } = useGLTF(gltfPath);
  
  return (
    <Canvas>
      <Suspense fallback={null}>
        <primitive object={scene} />
        <OrbitControls />
      </Suspense>
    </Canvas>
  );
}
```

### Feature 3: Shader Generator

Prompt-to-shader with live preview:

```python
# backend/tools/shader_gen.py
async def generate_shader(description: str) -> str:
    """Generate GLSL/ShaderToy compatible shader code."""
    
    system_prompt = """You are a shader programming expert.
    Generate GLSL fragment shaders that compile on ShaderToy.
    
    Rules:
    - Use standard uniforms: u_time, u_resolution
    - No external textures unless specified
    - Include comments explaining the math
    - Optimize for performance
    """
    
    response = await chat_completion(
        model="local/fable-fusion-27b",
        system=system_prompt,
        user=description
    )
    
    return extract_shader_code(response)
```

## 🐛 Bug Testing Checklist

### Test Local Endpoint
```bash
# 1. Start Fable-Fusion server
C:\AI\fable\FABLE-FUSION-27B.bat

# 2. Test with curl
curl http://127.0.0.1:8082/v1/models
curl http://127.0.0.1:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"local/fable-fusion-27b","messages":[{"role":"user","content":"Hello"}]}'
```

### Test Infinity Code Integration

1. **Start everything**: `FABLE-INFINITY.bat`
2. **Check backend**: `curl http://127.0.0.1:8000/health`
3. **Test chat**: Send a message, verify response
4. **Test swarm**: Set effort to "ultracode", run a task
5. **Check costs**: Should be $0 for local models

### Common Issues

| Issue | Fix |
|-------|-----|
| Port 8082 in use | `taskkill /IM llama-server.exe /F` |
| VRAM OOM | Close Chrome/Steam before starting |
| Backend won't start | Check `backend/config.yaml` syntax |
| Model not found | Download IQ4_XS quant to `C:\HotModels\` |

## 📈 Next Steps

1. **Download the 27B model**: Use `hf download` or browser
2. **Test FABLE-FUSION-27B.bat**: Verify single model works
3. **Test FABLE-INFINITY.bat**: Try the full stack
4. **Collect feedback**: Log issues, improve prompts
5. **RAG your repos**: Point Infinity Code at your codebases

## 🎯 Training Your Data Into the Model

Since you want the model to "train itself" — here's the realistic path:

### Option A: RAG (Retrieval Augmented Generation) - **Easiest**

```python
# Point Infinity Code at your codebases
# backend/skills/rag_indexer.py

from langchain.vectorstores import Chroma
from langchain.embeddings import HuggingFaceEmbeddings

# Index your repos
loader = GitLoader(repo_path="C:/YourGameProject", branch="main")
docs = loader.load()

# Chunk and embed
splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
chunks = splitter.split_documents(docs)

# Store in vector DB
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=HuggingFaceEmbeddings(model_name="BAAI/bge-large-en"),
    persist_directory="./chroma_db"
)

# Now 27B fusion queries this DB automatically
```

### Option B: LoRA Fine-tuning - **Best Results**

1. Curate ~10K examples from your best code
2. Train LoRA for 3-5 epochs
3. Merge with base 27B
4. Quantize to GGUF
5. Replace `C:\HotModels\` file

### Option C: Continuous Learning - **Experimental**

```yaml
# backend/config.yaml
retrain:
  provider: "together"
  api_key: "${TOGETHER_API_KEY}"
  base_model: "Qwen/Qwen3-Coder-Next"
  min_examples: 100  # Auto-trigger training at 100 accepted sessions
```

## 🌟 Pro Tips

1. **Use ultracode effort** for complex architecture tasks
2. **Vision loop enabled** for UI/3D tasks  
3. **Speculative decoding** is on by default (ngram-mod)
4. **Temperature 0.6** for code, 1.0 for creative
5. **Close games** before starting — you need that VRAM

---

**Happy coding with your 700+ ARC-C intelligence!** 🚀
