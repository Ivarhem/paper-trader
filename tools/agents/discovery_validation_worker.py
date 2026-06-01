#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
DISCOVERY_LOGICS=[
 'volatility_contraction_breakout_l40_vr75_q62','volatility_contraction_breakout_l60_vr70_q64',
 'pullback_uptrend_pb4_q62','pullback_uptrend_pb7_q64',
 'relative_strength_persistence_r8_q60','relative_strength_persistence_r14_q64',
 'quality_pullback_uptrend_pb3_q70','quality_pullback_uptrend_pb5_q72',
 'quality_breakout_l40_q70','quality_breakout_l60_q72',
 'stable_relative_strength_r8_q68','stable_relative_strength_r12_q70']

def main():
 ap=argparse.ArgumentParser(description='Priority validation lane for discovery strategy families only')
 ap.add_argument('--batch-size',type=int,default=600); ap.add_argument('--output',default='/tmp/discovery_validation_latest.json'); ap.add_argument('--logic-filter',default='')
 args=ap.parse_args(); out=Path(args.output)
 requested=[x.strip() for x in (args.logic_filter or '').split(',') if x.strip()]
 selected=requested if requested else DISCOVERY_LOGICS
 cmd=[sys.executable,'tools/agents/simulation_validation_worker.py','--batch-size',str(args.batch_size),'--logics',','.join(selected),'--output',str(out)]
 cp=subprocess.run(cmd,cwd=str(ROOT),text=True,capture_output=True)
 payload={}
 try: payload=json.loads(out.read_text(encoding='utf-8'))
 except Exception: payload={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'discovery_validation_worker','status':'invalid_child_output'}
 payload['mode']='discovery_validation_worker'
 payload['discovery_logics']=DISCOVERY_LOGICS
 payload['selected_logics']=selected
 payload['logic_filter_requested']=requested
 payload['child_returncode']=cp.returncode
 payload['child_stderr_tail']=cp.stderr[-2000:]
 out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(payload,ensure_ascii=False,indent=2))
 sys.exit(cp.returncode)
if __name__=='__main__': main()
