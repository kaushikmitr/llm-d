#!/usr/bin/env python3
"""Cloud Monitoring dashboard for the kv-fleet benchmark: llm-d vs Dynamo engines, both routers, GPUs."""
import json
NS='namespace="kv-fleet"'
W=[]; y=0
def sec(title, sub=''):
    global y
    W.append({"yPos":y,"height":4,"width":48,"widget":{"title":title,"sectionHeader":{"dividerBelow":True,"subtitle":sub}}}); y+=4
def row(*charts):
    global y
    w=48//len(charts)
    for i,(title,qs,unit) in enumerate(charts):
        ds=[{"plotType":"LINE","targetAxis":"Y1","legendTemplate":leg,"minAlignmentPeriod":"60s",
             "timeSeriesQuery":{"prometheusQuery":q,"unitOverride":unit}} for leg,q in qs]
        W.append({"xPos":i*w,"yPos":y,"height":16,"width":w,"widget":{"title":title,"xyChart":{"chartOptions":{"mode":"COLOR"},"dataSets":ds,"yAxis":{"scale":"LINEAR"}}}})
    y+=16
def q(p,expr): return f'histogram_quantile({p}, sum by (le, job, ${{__groupby}}) (rate({expr}[2m])))'
V=f'job=~"llmd-vllm|dynamo-workers",{NS}'
W.append({"yPos":0,"height":6,"width":48,"widget":{"title":"","text":{"format":"MARKDOWN","content":
 "### KV fleet: llm-d vs Dynamo on B200\nEngines are vLLM in both stacks, split by `job`: **llmd-vllm** = llm-d model servers, **dynamo-workers** = Dynamo workers. "
 "Router sections are stack-specific. Scrape configs: `~/kv-whitepaper/fleet/podmonitoring.yaml`."}}}); y=6
sec("Throughput and reuse","Per stack, summed over replicas")
row(("Tokens / s (prompt + generated)",[("{{job}}",f'sum by (job, ${{__groupby}}) (rate(vllm:prompt_tokens_total{{{V}}}[2m]) + rate(vllm:generation_tokens_total{{{V}}}[2m]))')],""),
    ("Generated tokens / s",[("{{job}}",f'sum by (job, ${{__groupby}}) (rate(vllm:generation_tokens_total{{{V}}}[2m]))')],""))
row(("Prefix-cache hit rate (token-weighted)",[("{{job}} HBM",f'sum by (job, ${{__groupby}}) (rate(vllm:prefix_cache_hits_total{{{V}}}[2m])) / sum by (job, ${{__groupby}}) (rate(vllm:prefix_cache_queries_total{{{V}}}[2m]))'),("{{job}} HBM + CPU tier",f'(sum by (job, ${{__groupby}}) (rate(vllm:prefix_cache_hits_total{{{V}}}[2m])) + sum by (job, ${{__groupby}}) (rate(vllm:external_prefix_cache_hits_total{{{V}}}[2m]))) / sum by (job, ${{__groupby}}) (rate(vllm:prefix_cache_queries_total{{{V}}}[2m]))')],"10^2.%"),
    ("Uncached prefill tokens / s",[("{{job}}",f'sum by (job, ${{__groupby}}) (rate(vllm:prompt_tokens_total{{{V}}}[2m]) - rate(vllm:prefix_cache_hits_total{{{V}}}[2m]))')],""))
sec("Engine load","Per replica")
row(("Requests running",[("{{job}} {{pod}}",f'sum by (job, pod, ${{__groupby}}) (vllm:num_requests_running{{{V}}})')],""),
    ("Requests waiting",[("{{job}} {{pod}}",f'sum by (job, pod, ${{__groupby}}) (vllm:num_requests_waiting{{{V}}})')],""))
row(("HBM KV-cache usage",[("{{job}} {{pod}}",f'avg by (job, pod, ${{__groupby}}) (vllm:kv_cache_usage_perc{{{V}}})')],"10^2.%"),
    ("Preemptions / s",[("{{job}}",f'sum by (job, ${{__groupby}}) (rate(vllm:num_preemptions_total{{{V}}}[2m]))')],""))
row(("CPU tier traffic (bytes / s, summed over replicas)",[("{{job}} store (GPU to CPU)",f'sum by (job, ${{__groupby}}) (rate(vllm:kv_offload_store_bytes_total{{{V}}}[2m]))'),("{{job}} load (CPU to GPU)",f'sum by (job, ${{__groupby}}) (rate(vllm:kv_offload_load_bytes_total{{{V}}}[2m]))')],"By/s"),
    ("CPU tier rewrite rate (% of the 1.61 TB tier per minute, per replica)",[("{{job}} {{pod}}",f'sum by (job, pod, ${{__groupby}}) (rate(vllm:kv_offload_store_bytes_total{{{V}}}[2m])) * 60 / 1.6106e12')],"10^2.%"))
row(("CPU-tier share of prefix hits (token-weighted)",[("{{job}}",f'sum by (job, ${{__groupby}}) (rate(vllm:external_prefix_cache_hits_total{{{V}}}[2m])) / sum by (job, ${{__groupby}}) (rate(vllm:prefix_cache_queries_total{{{V}}}[2m]))')],"10^2.%"),
    ("Steps / s",[("{{job}}",f'sum by (job, ${{__groupby}}) (rate(vllm:iteration_tokens_total_count{{{V}}}[2m]))')],""))
sec("Engine latency","p50 and p90 per stack, from the engines")
row(("Time to first token",[("p50 {{job}}",q(0.5,f'vllm:time_to_first_token_seconds_bucket{{{V}}}')),("p90 {{job}}",q(0.9,f'vllm:time_to_first_token_seconds_bucket{{{V}}}'))],"s"),
    ("Inter-token latency",[("p50 {{job}}",q(0.5,f'vllm:inter_token_latency_seconds_bucket{{{V}}}')),("p90 {{job}}",q(0.9,f'vllm:inter_token_latency_seconds_bucket{{{V}}}'))],"s"))
row(("End-to-end request latency",[("p50 {{job}}",q(0.5,f'vllm:e2e_request_latency_seconds_bucket{{{V}}}')),("p90 {{job}}",q(0.9,f'vllm:e2e_request_latency_seconds_bucket{{{V}}}'))],"s"),
    ("Queue time",[("p50 {{job}}",q(0.5,f'vllm:request_queue_time_seconds_bucket{{{V}}}')),("p90 {{job}}",q(0.9,f'vllm:request_queue_time_seconds_bucket{{{V}}}'))],"s"))
R=f'job="llmd-router",{NS}'
sec("llm-d router (EPP)","Metric names are llm_d_epp_* in this router build; needs --metrics-endpoint-auth=false")
row(("Requests / s (router)",[("{{model_name}}",f'sum by (model_name, ${{__groupby}}) (rate(llm_d_epp_request_total{{{R}}}[2m]))')],""),
    ("Requests running (router view)",[("running",f'sum by (${{__groupby}}) (llm_d_epp_request_running{{{R}}})')],""))
row(("Scheduling attempts / s by status",[("{{status}}",f'sum by (status, ${{__groupby}}) (rate(llm_d_epp_scheduler_attempts_total{{{R}}}[2m]))')],""),
    ("Ready endpoints in pool",[("{{name}}",f'sum by (name, ${{__groupby}}) (llm_d_epp_ready_endpoints{{{R}}})')],""))
row(("Router-predicted prefix hit (token-weighted)",[("chosen endpoint",f'sum by (${{__groupby}}) (rate(llm_d_epp_prefix_predicted_cached_tokens_sum{{{R}}}[2m])) / sum by (${{__groupby}}) (rate(llm_d_epp_prefix_prompt_tokens_sum{{{R}}}[2m]))'),("best available",f'sum by (${{__groupby}}) (rate(llm_d_epp_prefix_best_available_cached_tokens_sum{{{R}}}[2m])) / sum by (${{__groupby}}) (rate(llm_d_epp_prefix_prompt_tokens_sum{{{R}}}[2m]))')],"10^2.%"),
    ("Affinity-filter decisions / s",[("{{outcome}}",f'sum by (outcome, ${{__groupby}}) (rate(llm_d_epp_prefix_cache_affinity_filter_decisions_total{{{R}}}[2m]))')],""))
L=f'job="llmd-vllm",{NS}'
row(("llm-d: router-predicted vs engine-measured prefix hit (token-weighted)",[
    ("router predicted, chosen endpoint",f'sum by (${{__groupby}}) (rate(llm_d_epp_prefix_predicted_cached_tokens_sum{{{R}}}[2m])) / sum by (${{__groupby}}) (rate(llm_d_epp_prefix_prompt_tokens_sum{{{R}}}[2m]))'),
    ("engine actual, HBM + CPU tier",f'(sum by (${{__groupby}}) (rate(vllm:prefix_cache_hits_total{{{L}}}[2m])) + sum by (${{__groupby}}) (rate(vllm:external_prefix_cache_hits_total{{{L}}}[2m]))) / sum by (${{__groupby}}) (rate(vllm:prefix_cache_queries_total{{{L}}}[2m]))'),
    ("engine actual, HBM only",f'sum by (${{__groupby}}) (rate(vllm:prefix_cache_hits_total{{{L}}}[2m])) / sum by (${{__groupby}}) (rate(vllm:prefix_cache_queries_total{{{L}}}[2m]))')],"10^2.%"))
row(("Prefix index entries (all servers)",[("entries",f'sum by (${{__groupby}}) (llm_d_epp_prefix_indexer_size{{{R}}})')],""),
    ("Time to first token (router)",[("p50",f'histogram_quantile(0.5, sum by (le, ${{__groupby}}) (rate(llm_d_epp_request_ttft_seconds_bucket{{{R}}}[2m])))'),("p90",f'histogram_quantile(0.9, sum by (le, ${{__groupby}}) (rate(llm_d_epp_request_ttft_seconds_bucket{{{R}}}[2m])))')],"s"))
D=f'job="dynamo-frontend",{NS}'
sec("Dynamo frontend and KV router")
row(("Requests / s",[("requests",f'sum by (${{__groupby}}) (rate(dynamo_component_router_requests_total{{{D}}}[2m]))')],""),
    ("Router KV hit rate (per request)",[("p50",f'histogram_quantile(0.5, sum by (le, ${{__groupby}}) (rate(dynamo_component_router_kv_hit_rate_bucket{{{D}}}[2m])))'),("mean",f'sum by (${{__groupby}}) (rate(dynamo_component_router_kv_hit_rate_sum{{{D}}}[2m])) / sum by (${{__groupby}}) (rate(dynamo_component_router_kv_hit_rate_count{{{D}}}[2m]))')],""))
row(("Time to first token (router)",[("p50",f'histogram_quantile(0.5, sum by (le, ${{__groupby}}) (rate(dynamo_component_router_time_to_first_token_seconds_bucket{{{D}}}[2m])))'),("p90",f'histogram_quantile(0.9, sum by (le, ${{__groupby}}) (rate(dynamo_component_router_time_to_first_token_seconds_bucket{{{D}}}[2m])))')],"s"),
    ("Router queue: pending requests",[("pending",f'sum by (${{__groupby}}) (dynamo_frontend_router_queue_pending_requests{{{D}}})')],""))
G=f'{NS}'
sec("GPUs (DCGM)","Pods in kv-fleet")
row(("GPU utilisation",[("{{pod}}",f'avg by (pod, ${{__groupby}}) (DCGM_FI_DEV_GPU_UTIL{{{G}}})')],"%"),
    ("HBM bandwidth active",[("{{pod}}",f'avg by (pod, ${{__groupby}}) (DCGM_FI_PROF_DRAM_ACTIVE{{{G}}})')],"10^2.%"))
row(("GPU power",[("{{pod}}",f'sum by (pod, ${{__groupby}}) (DCGM_FI_DEV_POWER_USAGE{{{G}}})')],"W"),
    ("HBM used",[("{{pod}}",f'sum by (pod, ${{__groupby}}) (DCGM_FI_DEV_FB_USED{{{G}}})')],"MiBy"))
dash={"displayName":"KV fleet: llm-d vs Dynamo (B200)","mosaicLayout":{"columns":48,"tiles":W},
      "dashboardFilters":[{"filterType":"RESOURCE_LABEL","labelKey":"cluster","valueType":"STRING"},{"filterType":"RESOURCE_LABEL","labelKey":"namespace","valueType":"STRING"}]}
json.dump(dash,open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'dashboard-kv-fleet.json'),'w'),indent=1)
print(len(W),'tiles')
