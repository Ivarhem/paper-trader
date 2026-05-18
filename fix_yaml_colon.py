from pathlib import Path
p=Path('configs/research_agents.yaml')
s=p.read_text()
s=s.replace('    role: Two-layer committee: research support and risk gate.','    role: "Two-layer committee: research support and risk gate."')
p.write_text(s)
