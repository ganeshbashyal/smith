"""Deterministic smoke evaluation for persona routing and V2 retrieval coverage."""
from __future__ import annotations
import json
from pathlib import Path
from assistant_api.routing import route_request

CASES = [("science", "Explain a technology research result"), ("finance", "Analyse ASX momentum and commodities"), ("engineering", "Diagnose a PLC automation fault"), ("building", "Assess Australian NCC insulation evidence"), ("business", "Compare business unit economics"), ("health", "Explain health evidence and urgency"), ("news", "Verify current geopolitical news"), ("sports", "Analyse Manchester United and Ferrari F1"), ("automotive", "Diagnose a Harley motorcycle issue"), ("general", "Help me compare options")]

def main():
    results=[]
    for expected, question in CASES:
        routed=route_request(question, 'llama3.1:8b', 'llama3.1:8b')
        actual=routed.persona
        results.append({'expected':expected,'actual':actual,'passed':actual==expected})
    payload={'passed':sum(x['passed'] for x in results),'total':len(results),'results':results,'status':'pass' if all(x['passed'] for x in results) else 'review'}
    out=Path('data/evaluations'); out.mkdir(parents=True,exist_ok=True); (out/'persona_v2_latest.json').write_text(json.dumps(payload,indent=2)); print(json.dumps(payload,indent=2))
if __name__=='__main__': main()
