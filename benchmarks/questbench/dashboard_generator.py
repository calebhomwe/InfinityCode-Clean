"""
QuestBench Dashboard Generator for Infinity Code
Creates visual HTML reports for benchmark results
"""
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class DashboardGenerator:
    """Generates HTML dashboard from benchmark results"""

    def __init__(self, results_dir: Path):
        self.results_dir = results_dir

    def generate_dashboard(
        self,
        results_file: Path,
        output_file: Path | None = None
    ) -> Path:
        """Generate HTML dashboard from results"""

        with open(results_file) as f:
            results = json.load(f)

        if output_file is None:
            output_file = (
                self.results_dir /
                f"dashboard_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.html"
            )

        html = self._build_html(results)

        with open(output_file, 'w') as f:
            f.write(html)

        logger.info(f"Generated dashboard: {output_file}")
        return output_file

    def _build_html(self, results: dict) -> str:
        """Build complete HTML dashboard"""
        summary = results["summary"]
        sessions = results["sessions"]

        overall = summary['averages']['overall_score']
        completion = summary['averages']['completion_rate'] * 100
        cascade = summary['averages']['cascade_break_score'] * 100
        credits = summary['averages']['credit_efficiency'] * 100
        ts = results['timestamp']
        count = results['sessions_count']

        dist_html = self._render_distribution(summary['score_distribution'])
        insights_html = self._render_insights(summary['insights'])
        worst_html = self._render_worst_sessions(summary['worst_sessions'])
        table_html = self._render_sessions_table(sessions)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Infinity Code QuestBench - {ts}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1e1e1e;
            color: #d4d4d4;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1, h2, h3 {{
            color: #569cd6;
        }}
        .metric-card {{
            background: #252526;
            border: 1px solid #3e3e42;
            border-radius: 8px;
            padding: 20px;
            margin: 10px 0;
        }}
        .metric-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}
        .metric {{
            background: #2d2d30;
            padding: 15px;
            border-radius: 6px;
            text-align: center;
        }}
        .metric-value {{
            font-size: 2em;
            font-weight: bold;
            color: #4ec9b0;
        }}
        .metric-label {{
            color: #9cdcfe;
            margin-top: 5px;
        }}
        .insight {{
            background: #3e2a1e;
            border-left: 4px solid #d16969;
            padding: 15px;
            margin: 10px 0;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #3e3e42;
        }}
        th {{
            background: #252526;
            color: #569cd6;
        }}
        .score-bar {{
            background: #3e3e42;
            height: 20px;
            border-radius: 10px;
            overflow: hidden;
            margin: 5px 0;
        }}
        .score-fill {{
            height: 100%;
            background: linear-gradient(90deg, #d16969 0%, #dcdcaa 50%, #4ec9b0 100%);
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Infinity Code QuestBench Results</h1>
        <p>Generated: {ts}</p>
        <p>Sessions analyzed: {count}</p>

        <div class="metric-grid">
            <div class="metric">
                <div class="metric-value">{overall:.1f}</div>
                <div class="metric-label">Overall Score</div>
            </div>
            <div class="metric">
                <div class="metric-value">{completion:.0f}%</div>
                <div class="metric-label">Completion Rate</div>
            </div>
            <div class="metric">
                <div class="metric-value">{cascade:.0f}%</div>
                <div class="metric-label">Cascade Break Score</div>
            </div>
            <div class="metric">
                <div class="metric-value">{credits:.0f}%</div>
                <div class="metric-label">Credit Efficiency</div>
            </div>
        </div>

        <div class="metric-card">
            <h2>Score Distribution</h2>
            {dist_html}
        </div>

        <div class="metric-card">
            <h2>Key Insights</h2>
            {insights_html}
        </div>

        <div class="metric-card">
            <h2>Worst Performing Sessions</h2>
            {worst_html}
        </div>

        <div class="metric-card">
            <h2>All Sessions</h2>
            {table_html}
        </div>
    </div>
</body>
</html>"""

    def _render_distribution(self, dist: dict) -> str:
        """Render score distribution as bars"""
        html = ""
        total = sum(dist.values())

        for bucket, count in dist.items():
            pct = (count / total * 100) if total > 0 else 0
            html += f"""
            <div style="margin: 10px 0;">
                <div>{bucket}: {count} sessions</div>
                <div class="score-bar">
                    <div class="score-fill" style="width: {pct}%"></div>
                </div>
            </div>"""

        return html

    def _render_insights(self, insights: list[str]) -> str:
        """Render insights as cards"""
        if not insights:
            return "<p>No critical insights. Everything looks good!</p>"

        html = ""
        for insight in insights:
            html += f'<div class="insight">{insight}</div>'

        return html

    def _render_worst_sessions(self, sessions: list[dict]) -> str:
        """Render worst performing sessions"""
        if not sessions:
            return "<p>No sessions to analyze.</p>"

        html = """<table>
        <tr>
            <th>Session ID</th>
            <th>Score</th>
            <th>Cascade Breaks</th>
        </tr>"""

        for s in sessions:
            html += f"""
            <tr>
                <td>{s['session_id']}</td>
                <td>{s['score']:.1f}</td>
                <td>{s['cascade_breaks']}</td>
            </tr>"""

        html += "</table>"
        return html

    def _render_sessions_table(self, sessions: list[dict]) -> str:
        """Render all sessions table"""
        html = """<table>
        <tr>
            <th>Session ID</th>
            <th>Overall</th>
            <th>Completion</th>
            <th>Cascade</th>
            <th>Credits</th>
            <th>Time</th>
            <th>Satisfaction</th>
        </tr>"""

        for s in sessions:
            html += f"""
            <tr>
                <td>{s['session_id']}</td>
                <td>{s['overall_score']:.1f}</td>
                <td>{s['completion_rate'] * 100:.0f}%</td>
                <td>{s['cascade_break_score'] * 100:.0f}%</td>
                <td>{s['credit_efficiency'] * 100:.0f}%</td>
                <td>{s['time_efficiency'] * 100:.0f}%</td>
                <td>{s['user_satisfaction'] * 100:.0f}%</td>
            </tr>"""

        html += "</table>"
        return html
