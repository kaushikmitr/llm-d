#!/usr/bin/env python3
"""Sample vLLM counters from every serving pod in kv-fleet every PERIOD seconds via the API-server proxy.
One CSV row per pod per sample. Pods: llm-d decode (label llm-d.ai/role=decode, :8000/metrics) and
Dynamo workers (label app=dynamo-decode, tries :8081 then :8000). Keys are the raw metric names."""
import subprocess, time, json, csv, os, re, sys
K=os.environ.get('KUBECTL','kubectl'); CTX=os.environ['KUBE_CONTEXT']; NS='kv-fleet'
OUT=sys.argv[1]; PERIOD=float(sys.argv[2]) if len(sys.argv)>2 else 15
WANT=re.compile(r'^(vllm:(prefix_cache_queries_total|prefix_cache_hits_total|external_prefix_cache_queries_total|external_prefix_cache_hits_total|prompt_tokens_total|generation_tokens_total|num_requests_running|num_requests_waiting|kv_cache_usage_perc|iteration_tokens_total_count|iteration_tokens_total_sum|num_preemptions_total|request_prefill_time_seconds_sum|request_decode_time_seconds_sum|request_queue_time_seconds_sum|request_success_total)|dynamo_component_[a-z_]*(prefix_cache|prompt_tokens|generation_tokens|num_requests_running|kv_cache_usage|iteration_tokens)[a-z_]*|vllm:[a-z_]*(offload|kv_transfer|connector)[a-z_]*)\b')
def kube(*a):
    return subprocess.run([K,'--context',CTX,'-n',NS,*a],capture_output=True,text=True,timeout=60).stdout
def pods():
    out=kube('get','pods','-l','llm-d.ai/role=decode','-o','jsonpath={range .items[*]}{.metadata.name},{.status.phase},llmd{"\\n"}{end}')
    out+=kube('get','pods','-l','app=dynamo-decode','-o','jsonpath={range .items[*]}{.metadata.name},{.status.phase},dynamo{"\\n"}{end}')
    return [tuple(l.split(',')) for l in out.splitlines() if l.endswith(('llmd','dynamo')) and ',Running,' in l]
def scrape(pod, ports):
    for p in ports:
        txt=kube('get','--raw',f'/api/v1/namespaces/{NS}/pods/{pod}:{p}/proxy/metrics')
        rows={}
        for line in txt.splitlines():
            if line.startswith('#'): continue
            m=WANT.match(line)
            if not m: continue
            name,rest=line.split('{',1) if '{' in line else (line.split(' ')[0],'} '+line.split(' ',1)[1])
            labels,val=rest.rsplit('} ',1) if '} ' in rest else ('',rest)
            key=name
            fr=re.search(r'finished_reason="([a-z]+)"',labels)
            if fr: key+=':'+fr.group(1)
            try: rows[key]=float(val)
            except ValueError: pass
        if rows: return p,rows
    return None,{}
new=not os.path.exists(OUT)
with open(OUT,'a',newline='') as f:
    w=csv.writer(f)
    if new: w.writerow(['ts','pod','kind','port','metric','value'])
    while True:
        t=time.time()
        try:
            for pod,_,kind in pods():
                port,rows=scrape(pod,[8000] if kind=='llmd' else [8081,8000,9090])
                for k,v in rows.items(): w.writerow([f'{t:.0f}',pod,kind,port,k,v])
                # GPU-side activity from the driver: mean over the 8 GPUs of SM-busy %, memory-controller-busy %, power W
                smi=kube('exec',pod,'--','sh','-c','PATH=$PATH:/usr/local/nvidia/bin nvidia-smi --query-gpu=utilization.gpu,utilization.memory,power.draw --format=csv,noheader,nounits')
                vals=[[float(x) for x in l.split(',')] for l in smi.splitlines() if l.count(',')==2]
                if vals:
                    for i,k in enumerate(('smi:sm_busy_pct','smi:mem_busy_pct','smi:power_w')):
                        w.writerow([f'{t:.0f}',pod,kind,'smi',k,sum(v[i] for v in vals)/len(vals)])
            f.flush()
        except Exception as e:
            print(time.strftime('%H:%M:%S'),'sample error',e,file=sys.stderr,flush=True)
        time.sleep(max(1,PERIOD-(time.time()-t)))
