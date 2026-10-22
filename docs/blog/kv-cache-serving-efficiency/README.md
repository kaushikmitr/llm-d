# KV Cache Is the Problem: A Framework for LLM Inference Serving

Oct 20, 2026 · @kaushik mitra · Draft v0.3

## TL;DR

Evaluating the efficiency of an LLM inference serving system can be reduced to a single ratio. By combining a workload's intrinsic prefix overlap, its mean input and output lengths, and five hardware numbers measured on the deployment (Appendix A), we compute a **roofline**: the most tokens per second the hardware can sustain for that workload. For each load level, the steady-state throughput the system served divided by this ceiling is its **serving efficiency η**.

The shortfall from the ceiling splits into three factors whose product is η:

1. **Client**: how much of the ceiling the client asked for: the throughput its sessions and their idle time between turns would get from an engine running at the hardware's limits.
2. **Reuse**: prompt tokens the system had to recompute, either because the request went to a replica that did not hold their KV or because it had been evicted.
3. **Engine**: what the execution engine wasted at the hit rate it had.

Separately, a run's arithmetic intensity, computed from the engine's token counters and the model's per-token costs, shows whether it was compute-bound or memory-bound. On an NVIDIA B200 fleet with a host KV tier, running an agentic coding workload, **llm-d** reaches 0.47 of the ceiling, **NVIDIA Dynamo** 0.22 and **round-robin** 0.14. The prefix hit rate each stack kept sets the ranking: llm-d keeps it at 0.90 or above until HBM fills with the sessions it is decoding, at 240 sessions; Dynamo and round-robin are at 0.76 and 0.62 at their lightest loads, with HBM a quarter full. The framework is for infrastructure engineers building or selecting serving stacks and for vendors who want standardised performance claims.

## 1. The question

This post argues that the performance of any LLM serving system can be captured by one efficiency ratio, obtained in three steps:

1. **The ceiling.** The most tokens per second the hardware could serve on the workload if every reusable prefix were reused, computed from the workload's intrinsic prefix overlap ρ (the hit rate an oracle cache with unlimited retention would reach), its mean input and output lengths I and O in tokens, and the hardware inputs measured in Appendix A.
2. **The measurement.** What the system actually served: its steady-state throughput measured at each of several increasing load levels on the same trace, taking the highest.
3. **The ratio and its diagnostics.** Divide the measurement by the ceiling to get η. Then split the shortfall into three factors: the load the client offered, reuse, and the engine. Separately, the run's arithmetic intensity, computed from the engine's token counters and the model's per-token costs, shows whether it was compute-bound or memory-bound.

Every mechanism a stack adds moves the reuse factor, the engine factor, or the number of sessions the fleet can hold, and nothing else.

### The centrality of the KV cache

Every token a transformer generates is computed from the key and value (KV) tensors of every preceding token. This **KV cache** is the only large state an inference system carries across tokens, conversation turns and prefix-sharing requests. Model weights are static and shared; activations are transient. So the costs that differ from one request to the next are all KV decisions: compute it or fetch it, where to keep it and for how long, which block to evict, and which replica the request should land on so that request and KV meet.

The industry has built specialised solutions at three layers of the stack:

- **Routers.** Prefix-cache-aware routing sends a request to the replica that holds its KV.
- **Pool topologies.** Prefill/decode disaggregation runs the two phases on different pools and ships the KV once.
- **Storage layers.** Tiered offload moves KV below the accelerator instead of dropping it; peer-to-peer sharing pulls it from a neighbour instead of recomputing it.

Seen from the KV cache, these are facets of one placement problem, and the question for any of them, alone or combined, is the same: how close does the fleet get to the throughput its hardware allows for this workload?

## 2. Compute or memory: where a run sits on the roofline

Section 2.1 draws the line between compute-bound and memory-bound from four numbers, introduced here and collected in the table of Section 3. h is the prefix-cache hit rate a run achieved: the fraction of prompt tokens, token-weighted, that were found cached instead of recomputed. U = (1 − h)·I is the uncached input, the prompt tokens that had to be computed. L is the resident context, the prompt plus the output so far, which every decode step reads from HBM. n is the batch, the requests decoding together in one step. The hit rate enters only through U: ρ places the ceiling, and a run's measured h places that run.

![Section 6 runs on the B200 fleet, cluster106, 20 October 2026 · FLOPs and bytes derived from sampled engine counters and the model shape (Appendix A); tokens restored from the host tier are not counted as recomputed · 9 runs, 3 stacks](figures/fig1-measured-roofline.png)

*Section 6 runs on the B200 fleet, cluster106, 20 October 2026 · FLOPs and bytes derived from sampled engine counters and the model shape (Appendix A); tokens restored from the host tier are not counted as recomputed · 9 runs, 3 stacks*

A roofline chart places a run by two numbers: on the x axis its arithmetic intensity, the FLOPs it performed per byte it read from HBM, and on the y axis the FLOP/s it achieved. The hardware caps it from two sides: a flat compute roof at the peak FLOP/s, and a sloped bandwidth roof, FLOP/s equal to bandwidth times arithmetic intensity, which binds steps that read more than they compute. The two roofs meet at the ridge, the arithmetic intensity at which a step's compute time equals its memory time: left of it a step spends longer reading memory than computing, right of it longer computing. The figure places the nine fleet runs of Section 6 on this chart. The dashed roofs are the B200 datasheet; the solid roofs are what this deployment was measured to reach (Appendix A), 53% of the datasheet bandwidth and the prefill rate at a 128k context, which puts the measured ridge at about 290 FLOP/B.

At a 0.98 hit rate the fleet sits on the bandwidth roof: each step streams the attention and projection weights, the experts its tokens select, and the KV of only about six decoding requests per replica, to produce one token each, so every byte read serves few FLOPs. As the hit rate falls, the same workload moves right and up, across the ridge onto the compute roof. The GPUs get busier and the useful token rate gets lower, because the compute is recomputing contexts that were evicted. The hit rate decides where on the roofline a serving system lands.

Which resource binds first follows from the rule of Section 2.1, which compares the uncached input per output token with the context. For the agentic trace of Section 6 the context is long, about 117k tokens, and with the hit rate near ρ a turn prefills only about 7k of it, so the fleet is memory-bound: reuse has removed the prefill, and the limit is how much KV the fleet can hold and how fast it can read it. A stack that loses reuse pushes the same trace across the rule's threshold into compute-bound, because recomputing the context becomes the work. The ceiling R of Section 3 is the throughput at which the first of the three resources saturates with h = ρ. Compute-bound and memory-bound in this post describe that ceiling, not a run; a run can sit far below the ceiling on either side.

The three costs become the ceiling through one rule, called the step-time law in this post: a step takes the longer of its compute time and its memory time, and the throughput a replica sustains is the most it can push through while every step stays within compute, bandwidth and HBM capacity. The equations are in Appendix B.

### 2.1 Where the walls are

The boundary between compute-bound and memory-bound has a closed form in terms of one hardware number, the ridge of the Section 2 figure expressed as a token count, the **ridge** r: the number of KV tokens the device can read from HBM in the time it takes to prefill one token,

```latex
r = \frac{\text{HBM bandwidth} / \text{KV bytes per token}}{\text{prefill tokens per second}}
```

r depends on the hardware and the model and on nothing else; no hit rate appears in it. For Qwen3-Coder-480B on 8 × B200, 0.53 × 64 TB/s of achieved bandwidth over 254 KB of KV per token is 133 million KV tokens per second, against a calibrated prefill rate of 37,217 tokens per second, so r is about 3,600. (The prefill rate falls with context length, so r grows with L; at a 128k context the figure would be about 5,800, which lowers the agentic threshold below from 45 to 28 and changes no conclusion.) One more cost belongs on the memory side: every step streams the weights it touches, W\_b bytes, once, shared by the n sequences decoding in it (for a mixture of experts, the experts touched by every token in the step, the prefill tokens included, so W\_b is evaluated at n(1 + U/O) tokens), so per sequence it reads like W\_b/(n·b) extra tokens of context, where b is the KV bytes per token (254 KB here). With that folded into an effective context L\_eff = L + W\_b(n)/(n·b), compute takes longer than the memory read in a step, and is said to dominate it, when

```latex
\frac{U}{O} > \frac{L + W_b(n)/(n\,b)}{r} - 1
```

that is, when the uncached input per output token exceeds the effective context measured in ridges, less one. Which term dominates depends on the batch n; a workload is called compute-bound or memory-bound only at the ceiling, where n is fixed and the dominating term has reached its limit. Capacity is not in the rule: it caps how many sequences a replica can hold, n ≤ C/(L·b), and enters the ceiling as a separate condition (Appendix B). For the agentic workload of Section 6 (L = 117k, U = 7.5k, O = 386), U/O = 19 against a threshold of 50 at the ceiling's batch of 29, so it is memory-bound; the runs in its table where compute dominates are recompute, not work. The figure folds the batch size into colour: for every context length and U/O it shows the smallest decoding concurrency at which compute dominates, and grey where memory dominates at every batch the memory fits. The nine fleet runs are placed by their measured hit rates: llm-d's runs up to 240 sessions sit in the grey, and the Dynamo and round-robin runs and llm-d at 300 sessions sit in the n = 1 band, where recompute dominates at any batch.

![the smallest decoding concurrency n at which U/O > (L + W_b(n)/(n·b))/r − 1 holds, for the B200 deployment of Appendix A (r = 3,600, W_b(n) from the expert-count formula of Appendix B, n capped by KV capacity and the engine's 64-sequence limit) · fleet runs from Section 6 placed by their measured hit rates (U = (1 − h)·I, h counting HBM and host-tier hits) · compute against bandwidth only; the capacity wall is a separate condition](figures/fig2-wall-map.png)

*the smallest decoding concurrency n at which U/O > (L + W_b(n)/(n·b))/r − 1 holds, for the B200 deployment of Appendix A (r = 3,600, W_b(n) from the expert-count formula of Appendix B, n capped by KV capacity and the engine's 64-sequence limit) · fleet runs from Section 6 placed by their measured hit rates (U = (1 − h)·I, h counting HBM and host-tier hits) · compute against bandwidth only; the capacity wall is a separate condition*

## 3. The ceiling and η

An inference engine serves requests in steps. One step is one forward pass over everything scheduled in it: a chunk of prefill tokens from prompts still being processed, plus one new token for every request that is decoding. Each step incurs three hardware costs:

1. **Compute.** Every token costs the model's active FLOPs plus attention over its request's context; a prefill chunk is almost all of it.
2. **Bandwidth.** Every step streams the weights it touches and reads the KV of every decoding request, so its memory time grows with the number and length of resident contexts.
3. **Capacity.** Every request whose KV is held, decoding or waiting for its next turn, occupies HBM; when HBM is full something is evicted and, unless a lower tier still holds it, must be recomputed when it returns.

The KV cache enters all three. A cached prompt token costs no compute (a **prefix-cache hit**); a resident context costs bandwidth every step; a retained context costs capacity whether or not it is decoding.

Serving efficiency η is one number per workload and hardware: the tokens per second per accelerator a system achieved, divided by the ceiling R. R is the tokens per second the same accelerators could sustain if every reusable prefix token were reused (h = ρ) and KV occupied HBM only while its request was decoding. Tokens are input plus output, so a cached prompt token counts as served just as a recomputed one does; what differs is what it cost.

```latex
\eta = \frac{\text{achieved tokens/s per accelerator}}{R(I,\ O,\ \rho;\ F,\ W,\ C,\ b,\ W_b,\ c)}
```

| Symbol | Meaning | How it is obtained |
| --- | --- | --- |
| I, O | Mean prompt and output length of the workload, in tokens, token-weighted over requests | From the trace or the request bodies; the harness's output count is not trusted (Section 6) |
| ρ | Intrinsic overlap: the prefix hit rate an oracle with unlimited retention would reach on this trace. ρ − h is the reuse a stack lost, to routing and to eviction from every tier | Computed from the trace once, at fleet capacity |
| h | Prefix-cache hit rate the system achieved, token-weighted | The engine's own counters: cached prompt tokens over prompt tokens |
| U | Uncached input: (1 − h) × I, the prompt tokens that must be computed | From h and I |
| L | Resident context: the whole prompt plus the output so far. Every decode step reads all of it from HBM, and it occupies capacity for as long as it is held | I + O/2, the average over a request's life |
| R | The ceiling: tokens per second per accelerator at the first saturated resource, with h = ρ. R alone means R(ρ); R(h) is the same formula at a run's achieved hit rate | Appendix B, from the calibrated inputs of Appendix A |
| η | Serving efficiency: achieved tokens per second per accelerator over R | Peak steady-state throughput over a sweep of offered load, divided by R |

R is evaluated at the workload's intrinsic overlap ρ, not at the hit rate the system achieved, so any reuse the system fails to find, keep or share shows up as a shortfall from R. It rests on three idealisations, each of which makes it an upper bound: KV occupies HBM only while its request is decoding, with paused sessions held elsewhere at no cost; prefill is spread evenly over the steps rather than run in whole chunks; and compute and memory traffic overlap fully within a step, so a step takes the longer of the two. Smaller omissions are listed in Section 8. Whether the workload is compute-bound or memory-bound is a property of the workload and the hardware, given by the rule of Section 2.1 at the ceiling's batch. How much a system serves depends on load, so η is taken at the peak of a sweep: record steady-state throughput at each of several offered loads and divide the highest by R.

Which resource a saturated run is pressing on can be read from the engine instead of inferred from latency. Requests waiting for KV blocks mean capacity. A step time that grows with the number and length of resident contexts, with HBM traffic near the calibrated bandwidth, means bandwidth. Steps filled with prefill chunks at the calibrated prefill rate, with HBM traffic low, mean compute. The runs of Section 6 show all three, and the measured roofline in Section 2 is the same reading drawn.

### The ceiling for the B200 fleet

On the B200 deployment calibrated in Appendix A, the agentic workload (I = 117k, O = 386, ρ = 0.94) has a ceiling of 27.6k tokens per second per GPU: 221k per replica and 1.33M for the six-replica fleet, capacity-bound at about 29 resident sequences and a 40 ms step. Compute is at 62% when capacity binds, so the deployment sits near its ridge. Output means come from the request bodies: the harness's own output count is wrong on about one turn in 200, recording up to 246k tokens for a 30 s turn, so each request's max\_tokens is used instead. The ceiling keeps KV in HBM only while a sequence decodes. At the ceiling the fleet completes 11.3 turns per second; with the trace's 12 s mean tool pause that is about 23 paused sessions per replica, whose KV, about 2.7M tokens, has to be held outside HBM for the ceiling to be reachable at all. The 6.19M-token host tier is what holds it here.

What it would take to reach R, in one picture: the figure draws the throughput the step-time law allows at h = ρ as the number of sequences decoding per replica grows. The line rises with the batch, because every step reads the weights once however many sequences share it, and ends where the batch fills HBM, at 29 sequences. A fleet whose HBM also had to hold the paused sessions would stop at 13; the host tier moves the wall. Reaching R therefore means three things: keep HBM for decoding sequences only, hold enough sessions that 29 of them are decoding per replica (about 54 live per replica, 320 in the fleet), and keep each step at the memory-read time the law assumes. The measured runs show which of these the stacks managed. llm-d at 120 sessions sits on the line with six decoding; at 180 it ran twelve but delivered 0.71 of what twelve allow, and at 240 eighteen at 0.52, as the engine's step times grew with chunked prefill and host-tier copies and then preemption. Dynamo and round-robin never pass twelve, because their lost reuse turns added sessions into recompute. Three consequences follow from the same law: without the host tier the wall sits at 13 sequences and the ceiling at 0.69 of R; FP8 KV halves the bytes per token and doubles the batch HBM holds, but raises R only 1.6 times, because compute then binds; and a stack that sustains twelve decoding per replica at the law's step time reaches 0.66 of R, so llm-d's 0.47 at that batch means its steps ran 1.4 times longer than the law allows.

![ideal curve from the step-time law of Appendix B at h = ρ for the B200 fleet of Appendix A; measured points from the nine runs of Section 6, at the mean number of requests decoding per replica over the steady window](figures/fig4c-ideal-throughput-vs-batch.png)

*ideal curve from the step-time law of Appendix B at h = ρ for the B200 fleet of Appendix A; measured points from the nine runs of Section 6, at the mean number of requests decoding per replica over the steady window*

### Reporting η

For η to be comparable across systems it must be published with: the Appendix A parameters and how the two efficiency factors were measured; token-weighted mean input and output lengths, with the output length's coefficient of variation; ρ and the oracle it came from; the offered rate or concurrency; and the client's sessions and per-turn idle time, so the client factor can be recomputed. Four traps account for most incomparable numbers today. Hit rates averaged per request instead of per token overweight short requests. Benchmarks that do not state their prefix structure (distinct prefixes, their length, turns per session, inter-turn gap) cannot be reproduced on the axis that matters. A 99% improvement in time to first token over a round-robin baseline, at a load that baseline cannot sustain, measures queueing, not caching. And a baseline carried over from another day, or a learned component measured cold, is a different experiment.

## 4. Where the shortfall goes

η judges a system against the hardware, but a measured run also reflects its load generator. The decomposition therefore treats the generator as a client whose behaviour is given, and uses only what the client controls and what the system did with it. In a closed loop the client controls two things: the number of sessions S it runs, and Z, how long each session sits idle between the server finishing a turn and the client sending the next (tool time plus anything else the client does). Neither needs the server to measure. Let T\_ideal be the per-turn service time of an engine running at the hardware's limits with full reuse (O decode steps, each lasting the longer of its compute and memory time at the run's batch, the step-time law of Section 3; not zero, since output is generated one token per step), T\_h the same at the achieved hit rate, and T the measured per-turn service time. Such an engine would see each session cycle in Z + T\_ideal, so the most this client could get from any server is S(I + O)/(Z + T\_ideal). Little's law then gives η as a product of three factors:

- **Client, S(I + O)/(Z + T\_ideal) over R, capped at 1.** How much of the ceiling the client asked for: what its sessions and idle time would get from an engine running at the hardware's limits. A property of the load generator, not the system.
- **Reuse, (Z + T\_ideal)/(Z + T\_h).** Time lost recomputing prompt tokens, either because the request went to a replica that did not hold their KV or because it had been evicted.
- **Engine, (Z + T\_h)/(Z + T).** Everything else the engine lost at the hit rate it had.

The split uses no measured batch size, which depends on how fast the server is, and no assumption about how the client should behave. Retention is not one of the factors: it sets where the peak falls, the number of sessions the system can hold before it starts evicting, and is reported beside η as a capacity. Section 6 computes all three factors for every run.

## 5. Where KV can be

![where KV can be and how it moves · one index, three moves](figures/fig3-where-kv-can-be.png)

*where KV can be and how it moves · one index, three moves*

A prompt token's KV is in one of three places relative to the replica that will serve the request: resident in that replica's accelerator memory, which costs nothing; somewhere it can be fetched from, at a price set by the slowest link on the path; or nowhere, at the prefill price of one token over R\_pf. The order of fetch prices is stable across hardware generations even as the numbers move: accelerator hit, then host memory over PCIe, then a peer's host memory over the network, then local disk, then recompute, then remote storage. Three moves bring request and KV together, and each is priced against the roofline.

**Moving the request.** Routing to the replica that holds the prefix raises h toward ρ at no transfer cost, but it concentrates load. The router's rule is a comparison in one unit: the warm replica's queued prefill, in tokens over R\_pf, against the cold replica's extra prefill, (1 + n\_cold)·Δu/R\_pf, where the factor counts the decode streams the cold prefill would stall. Van Drie's companion derivation adds a memory toll to the cold side that grows as the fleet's safe HBM utilisation approaches one. That reproduces the map: balance matters when compute binds, affinity matters when memory binds.

**Moving the KV.** A prefix held on a peer's host tier can be pulled host to host in T\_0 + L·b/B and recomputed in L/R\_pf, so the pull wins above a crossover length T\_0/(1/R\_pf − b/B), about 1,200 tokens for gpt-oss-120b on RDMA with the fitted T\_0 and B. Demoting KV to a host tier instead of evicting it keeps h at ρ as long as the idle working set fits. For sessions that go quiet between turns, the idle set is concurrent sessions times their average context; when it exceeds what the fleet retains, the engine's LRU evicts and h falls to ρ times the retained fraction. A phase-staggered batch needs only O tokens of headroom for growth whatever its size, because every completion frees what the batch just grew; a synchronised burst of n requests, which is what an agent's fan-out produces, grows by n·O with nothing to recycle. An interactive Rung 0 simulation of both cases is at https://claude.ai/artifact/EBq2S6B3iiC7MVJquEw4ei.

**Moving the compute.** Running prefill on a separate pool and shipping the KV once removes prefill's interference with decode and pays I·b/B + T\_0 per request on the interconnect. The companion derivation gives the threshold: split when decode concurrency exceeds R\_pf/B\_tok, corrected for setup latency and for history already resident on the decode side. Under a split, the history the decode pool generates lives there while the next turn's prefill happens elsewhere, so the two pools need a shared tier or a peer pull, or the conversation is recomputed every turn.

Eviction is not a move. It is what happens when every tier is full: a block is dropped, its next use pays a full recompute, and the only decision left is which block to drop.

## 6. Empirical evaluation: three stacks on a B200 fleet

### Headline result

On one six-replica B200 fleet with a 1.6 TB host KV tier per replica, llm-d serves 622k tokens per second at 180 sessions, η = 0.47 against a ceiling of 1.33M tokens per second. NVIDIA Dynamo's KV router serves 287k at 90 sessions, η = 0.22. A round-robin Service serves 191k at 90 sessions, η = 0.14. The prefix hit rate each stack kept, counting hits in HBM and in the host tier, orders the three: 0.92, 0.74 and 0.62 at their peaks, against the 0.98 an unlimited-retention oracle reaches over the same steady window (0.94 over the whole trace, counting each conversation's first turn). llm-d is the only stack whose peak is set by hardware capacity; the other two peak with HBM about a quarter full.

### Setup

Three stacks were evaluated on one six-replica NVIDIA B200 fleet running the agentic coding workload of the llm-d agentic-serving guide: Qwen3-Coder-480B-A35B-FP8 at TP=8 with expert parallelism, on vLLM v0.28.0 (the version pinned inside Dynamo v1.5.0's runtime image), with the same six nodes, engine flags, KV allocation (3,403,712 tokens in HBM per replica), trace and seeds. Every replica also had a 1.61 TB host KV tier (vLLM's OffloadingConnector, write-through, 6.19M tokens), registered with the GPUs in 64 GiB pieces through a backport of vLLM PR #51081, since one registration of a tier above 512 GiB fails on this platform.

1. **llm-d**, in the default configuration of its agentic-serving guide: the approximate prefix index (hashed prompt prefixes scored against each replica's recent history), a prefill-rate affinity filter calibrated to the measured 37,217 tokens per second, and the token-load scorer. The index was set to 193,500 blocks per replica. Its tokenizer-free estimate counts 4 bytes as one token, about 1.5 real tokens for this code-heavy trace, so that setting covers about 128k engine blocks: more than the host tier alone, less than both tiers together. Its precise index (KV events from each engine) was not used.
2. **NVIDIA Dynamo**, routing through its frontend in KV mode with default cost weights and 64-token blocks, fed by the engines' KV events, with self-describing offload events enabled so that the router sees the host tier as its documentation requires.
3. **Round-robin**, a plain Kubernetes Service, as the baseline.

Each stack ran an ascending ladder of session counts until its throughput had fallen: llm-d at 120, 180, 240 and 300 sessions; Dynamo at 60, 90 and 120; round-robin at 90 and 120. Throughput is steady-state, and η is taken at each stack's peak over its ladder (Section 3). The engines' counters were sampled every 15 s (uncached prefill tokens, tokens restored from the host tier, generated tokens, steps, resident and waiting requests, KV occupancy, preemptions); achieved FLOP/s, HBM traffic and arithmetic intensity follow from them and the model shape. Every level ran on all six replicas, checked from each replica's own counters.

The trace comes from inference-perf's conversation replay: a 3,000-token system prompt shared by all conversations; a per-conversation prefix of about 160k tokens on average (lognormal, clipped to 10k to 200k); 20 turns per conversation, each adding about 1,500 new input tokens and the previous output, itself about 425 tokens on average; and a tool pause between turns of 15 s on average (lognormal, 1 to 100 s). Mean prompt length varies with the level's seed, which is why the decomposition scores each run at its own. What each level delivered over the steady window, from the per-request records and the engine counters:

| Stack | Sessions configured | Sessions live, mean | Mean prompt I, tokens | Mean output O, tokens | Tool pause, mean | Other idle per turn | Engine time per turn | Requests in the engine per replica, running + waiting |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| llm-d | 120 | 103 | 132k | 417 | 13.5 s | 4.0 s | 8.9 s | 5.9 + 0.1 |
| llm-d | 180 | 166 | 134k | 387 | 12.8 s | 3.7 s | 22.4 s | 11.9 + 4.2 |
| llm-d | 240 | 217 | 129k | 406 | 13.4 s | 6.7 s | 34.3 s | 18.1 + 6.3 |
| llm-d | 300 | 272 | 140k | 390 | 13.5 s | 13.2 s | 95.0 s | 20.9 + 19.1 |
| Dynamo KV router | 60 | 54 | 135k | 419 | 13.2 s | 3.6 s | 15.2 s | 4.0 + 0.4 |
| Dynamo KV router | 90 | 81 | 118k | 400 | 12.2 s | 3.8 s | 21.1 s | 7.5 + 0.8 |
| Dynamo KV router | 120 | 107 | 129k | 392 | 13.7 s | 7.3 s | 36.2 s | 11.4 + 1.6 |
| round-robin Service | 90 | 79 | 114k | 394 | 12.5 s | 7.2 s | 34.0 s | 7.7 + 1.7 |
| round-robin Service | 120 | 103 | 125k | 400 | 14.1 s | 13.5 s | 55.9 s | 10.8 + 3.2 |

A session is live while it has a turn outstanding or is in its tool pause; the rest of the configured sessions are between turns in the harness. Sessions live divided by six, times the engine share of the cycle, is the batch each replica decodes: about six at 120 sessions for llm-d, twelve at 180.

### Results summary

| Stack | Sessions | Steady tok/s | η | TTFT median | Hit rate, HBM + host | of which host tier | HBM used | Preemptions / min | HBM bandwidth, % of peak | FP8 compute, % of peak | AI, FLOP/B |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| llm-d | 120 | 604k | 0.46 | 1.2 s | 0.98 | 0.00 | 23% | 0 | 57 | 4 | 36 |
| llm-d | 180 | 622k | 0.47 | 1.5 s | 0.92 | 0.22 | 43% | 0 | 33 | 10 | 172 |
| llm-d | 240 | 570k | 0.43 | 2.1 s | 0.90 | 0.66 | 65% | 2.2 | 27 | 11 | 239 |
| llm-d | 300 | 345k | 0.26 | 36 s | 0.75 | 0.73 | 85% | 6.6 | 15 | 20 | 737 |
| Dynamo KV router | 60 | 253k | 0.19 | 1.6 s | 0.76 | 0.08 | 14% | 0 | 36 | 13 | 200 |
| Dynamo KV router | 90 | 287k | 0.22 | 2.1 s | 0.74 | 0.18 | 23% | 0 | 27 | 14 | 283 |
| Dynamo KV router | 120 | 271k | 0.20 | 3.3 s | 0.67 | 0.21 | 40% | 0 | 17 | 18 | 600 |
| round-robin Service | 90 | 191k | 0.14 | 4.1 s | 0.62 | 0.13 | 26% | 0 | 27 | 15 | 303 |
| round-robin Service | 120 | 180k | 0.14 | 10.2 s | 0.53 | 0.16 | 41% | 0 | 19 | 19 | 549 |

![B200 fleet experiment, cluster106, 20 October 2026 · inference-perf v0.7.0 conversation replay, 32 workers · engine counters sampled every 15 s; hit rate counts HBM and host-tier hits · 9 runs, 3 stacks](figures/fig4-eta-vs-hit-rate.png)

*B200 fleet experiment, cluster106, 20 October 2026 · inference-perf v0.7.0 conversation replay, 32 workers · engine counters sampled every 15 s; hit rate counts HBM and host-tier hits · 9 runs, 3 stacks*

![B200 fleet experiment, cluster106, 20 October 2026 · steady-window throughput, mean HBM KV occupancy per replica and preemptions from engine counters · 9 runs, 3 stacks](figures/fig4b-throughput-and-hbm-vs-sessions.png)

*B200 fleet experiment, cluster106, 20 October 2026 · steady-window throughput, mean HBM KV occupancy per replica and preemptions from engine counters · 9 runs, 3 stacks*

The second figure shows the resource that sets llm-d's peak. Its throughput rises to 180 sessions and falls after, and the lower panel shows why: HBM occupancy climbs from 23% to 85% as sessions are added, and from 240 sessions the engine preempts running requests to make room. Dynamo and round-robin peak while HBM is about a quarter full, so capacity is not what limits them.

### Key takeaways

- **The knee is HBM capacity for the sessions being decoded.** llm-d keeps the hit rate at the workload's level, 0.98 to 0.90, from 120 to 240 sessions, with the host tier supplying up to two thirds of the hits. Past 180 sessions the KV of the requests decoding at once fills HBM: at 240, 18 run per replica, HBM averages 65% and the engine starts preempting; at 300, with HBM at 85% and 21 running, preemptions reach 6.6 a minute and median time to first token is 36 s. A preempted request is restored from the host tier rather than recomputed, but it waits for a slot and for the copy, so turns get longer and throughput falls while the hit rate holds.
- **The hit rate explains the ranking.** Dynamo's router kept 0.76 to 0.67 and round-robin 0.62 to 0.53, against the 0.98 an oracle reaches over the same window, and both peak with HBM about a quarter full and no preemptions. Their shortfall is routing: a turn lands on a replica that holds none of its context in either tier, and the prompt is recomputed. Dynamo ran with default routing weights and the stock frontend, as a user following its documentation would deploy it, and neither stack was tuned beyond llm-d's documented prefill-rate calibration.
- **The arithmetic intensity follows the hit rate.** With the hit rate near ρ the fleet sits on the bandwidth side of the ridge (57% of HBM bandwidth and 4% of compute for llm-d at 120 sessions); as the hit rate falls, recompute carries every stack toward and across the measured ridge of about 290 FLOP/B (Dynamo at 120 sessions 600, round-robin at 120 sessions 549, llm-d at 300 sessions 737), where the GPUs are busier and the useful token rate is lower.

### The load the harness delivered

inference-perf's conversation replay (v0.7.0) advances conversations in lockstep when used as documented, so the runs set the concurrency to four times the number of conversations, run 20 turns per conversation and use 32 worker processes; the engines still never see more than one request per conversation. With that, the harness kept 86 to 92% of the configured sessions in flight, with 4 to 7 s of idle time per turn beyond the tool pause at the peaks and 13 s at the collapsed levels; the decomposition carries this as the client factor, so the ranking does not depend on it. The upstream fix, one closed loop per conversation that records its own idle time, would make the workaround unnecessary.

### Where the loss is

Three factors per run, as defined in Section 4. Z is the time from a turn's last token to the session's next request, and T the time from a request's start to its last token, both from the per-request records. Each run is scored against the ceiling at its own mean prompt length, so η here differs slightly from the headline table. T\_ideal is taken at the oracle's hit rate over the steady window, 0.98, since h is measured over that window.

| Stack | Sessions | Hit rate | Z, idle per turn | T, service per turn | Client | Reuse | Engine | η |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| llm-d | 120 | 0.98 | 17.5 s | 8.9 s | 0.52 | 1.00 | 0.96 | 0.50 |
| llm-d | 180 | 0.92 | 16.4 s | 22.4 s | 0.69 | 1.00 | 0.68 | 0.47 |
| llm-d | 240 | 0.90 | 20.0 s | 34.3 s | 0.75 | 1.00 | 0.61 | 0.45 |
| llm-d | 300 | 0.75 | 26.8 s | 95.0 s | 0.79 | 0.69 | 0.49 | 0.26 |
| Dynamo KV router | 60 | 0.76 | 16.8 s | 15.2 s | 0.29 | 0.95 | 0.77 | 0.21 |
| Dynamo KV router | 90 | 0.74 | 16.0 s | 21.1 s | 0.35 | 0.92 | 0.70 | 0.23 |
| Dynamo KV router | 120 | 0.67 | 21.0 s | 36.2 s | 0.39 | 0.73 | 0.74 | 0.21 |
| round-robin Service | 90 | 0.62 | 19.8 s | 34.0 s | 0.29 | 0.81 | 0.64 | 0.15 |
| round-robin Service | 120 | 0.53 | 27.6 s | 55.9 s | 0.32 | 0.67 | 0.66 | 0.14 |

The split reads this way. The client factor is the load's own: at llm-d's peak the sessions and their idle time asked for 0.69 of the ceiling. Within that, llm-d lost nothing to reuse up to 240 sessions and 32 to 39% to the engine, the time requests spend waiting to be scheduled and, past 240, preempted; at 300 it also lost 31% to reuse. Dynamo and round-robin peak at lower loads, so their client factor is 0.29 to 0.39; past their peaks, added sessions turn into recompute (reuse 0.92 to 0.67) while the engine factor stays at 0.64 to 0.77. One idealisation of Section 3 shows in the table: the step-time law spreads prefill evenly across decode steps, so at a small batch recompute hides under the memory time and the reuse factor understates it, as for Dynamo at 60 sessions (hit rate 0.76, reuse 0.95); the engine factor carries it instead. At 120 sessions llm-d's 0.98 is the oracle's own hit rate over the window, so its reuse is 1.00. The rule of Section 2.1, applied to each run's measured hit rate, context and batch size, puts all nine runs on the same side of the measured ridge (about 290 FLOP/B, where the solid roofs of the Section 2 figure meet) as their arithmetic intensity. Both use the same per-token costs, so this shows the two are consistent; it is not an independent measurement.

### Run notes

Per-replica throughput stayed within about 10% of each level's mean, except round-robin at 120 sessions, where two replicas held 17 running requests each against 6 to 10 on the others, and 0 to 6 requests per level were lost to disconnects.

## 7. Doing this for your own workload

Four steps, each one script or one job, give η and its split for any workload on any deployment.

1. **Characterise the trace.** From the per-request records: mean input and output lengths I and O, turns per session and the pause between them, and ρ from an oracle replay in which every prefix stays cached. Count ρ over the whole trace, each session's first turn included.
2. **Calibrate the deployment.** Two jobs on one replica: prefill throughput at the trace's context, and the decode KV-read rate from the step-time difference between one and four long-context decodes. With KV bytes per token, HBM available for KV and weight bytes per step, that is all R needs (Appendix A).
3. **Compute R and what binds.** The closed form of Appendix B, or the step-time law iterated over the batch. If capacity binds, note the batch at the wall and the KV the paused sessions need outside HBM: turn rate × pause × context. That number sizes a host tier before one is bought.
4. **Sweep the load and split the shortfall.** Run an ascending ladder of session counts; at each level take the steady window and record the hit rate in every tier, HBM occupancy, running and waiting requests, preemptions, step rate, and per-request Z and T. η is the peak over R; client, reuse and engine follow from Section 4.

The split then names the lever:

| What you see | Factor | Lever |
| --- | --- | --- |
| Hit rate well below ρ while HBM is far from full and nothing is preempted | Reuse | Routing: turns land on replicas that do not hold their context. Fix the index or the affinity before adding memory. |
| Hit rate holds at low load and falls as sessions are added, with HBM filling | Reuse, then engine | Retention: paused sessions are being evicted. A host tier, or fewer sessions per replica. |
| Hit rate holds, HBM near full, preemptions, step times growing | Engine, from capacity | More HBM for decoding KV: fewer bytes per token, shorter contexts, a tier for everything not decoding. |
| Hit rate holds, HBM half empty, requests waiting, step times above the law | Engine, from scheduling | Chunk size and admission: prefill chunks stalling decodes, tier copies in the critical path. |
| Sessions idle beyond their tool pause, few requests in flight | Client | The load generator, not the stack. Fix it before concluding anything. |
| All three factors near one and η still low | The ceiling | The hardware is what it is. Compare deployments, not stacks. |

Section 6 is this procedure applied once. Doing it took an effort like this post; the scripts, configs and calibration jobs that did it are in the [reproduction kit](https://github.com/kaushikmitr/llm-d/tree/kv-cache-serving-efficiency-blog/docs/blog/kv-cache-serving-efficiency/reproduce), so the next one is cheaper.

## 8. What the ceiling leaves out

The ceiling is a first-order bound on purpose: it keeps the terms that decide which resource binds and drops the rest. Each omission below pushes the ceiling in a known direction, and for the headline workload only one of them matters, the one Section 6 already names.

| Left out of the ceiling | What it does to the ceiling | Size on the B200 headline workload |
| --- | --- | --- |
| Attention FLOPs grow with context; the compute term is linear in tokens | Too high for compute-bound long-context recompute (about 35% at 48k tokens) | None at the ceiling: it is capacity-bound at ρ, and the calibrated prefill rate at 128k already carries the attention cost |
| Sequences sharing a prefix share its KV blocks and reads; the capacity and bandwidth terms charge each in full | Too low for shared-prefix workloads: the llm-d optimized-baseline guide's shared-prefix run scores 1.48 against it | None: agentic sessions share nothing |
| Output-length variance, the (1 + CV²) factor on resident context | Too high when outputs are long and skewed | Negligible: 386 output tokens against a 117k context |
| The small-batch step floor, launch and collective overhead (5.8 to 7.9 ms with nothing to read) | None at saturation | None; it sets the latency of the low-load runs, not the ceiling |
| Steps assumed evenly mixed; an engine that fills each step with an 8k prefill chunk stalls its decodes for 220 to 310 ms | Not a ceiling term by design: it is an engine property | Caps the fleet at about 19k tokens per second per GPU, 0.69 of the ceiling; it appears in the engine factor of Section 6, not in R |
| KV of paused sessions assumed held outside HBM at no cost | Too high for a fleet with HBM alone; right once a host tier holds them, as on this fleet | First-order for llm-d's knee: with paused sessions in the host tier, HBM fills with the sessions being decoded, from about 18 per replica against the ceiling's 29, because the engine also holds the chunked prefill of waiting requests and the KV it is copying in |

None of these changes the order of the stacks or the hit-rate finding. They bound how far the 0.47 could move, and only the last matters at first order.

## 9. Conclusion

The roofline framework reduces serving evaluation to one ratio, η, and splits its shortfall into three factors: the load the client offered, reuse, and the engine. Infrastructure engineers get a diagnostic that says which lever to pull: routing and eviction policy when reuse is lost, scheduling when the engine is, and retention and offload when the peak is set by how many sessions the system can hold. Vendors get a number comparable across stacks and hardware, provided the workload's ρ, I and O, the calibration and the client's offered load are published with it. On the B200 fleet measured here, the ranking llm-d 0.47, Dynamo 0.22, round-robin 0.14 is set by the hit rate each stack kept. llm-d's remaining gap splits between load the client did not offer and an engine that slows as HBM fills with the sessions it is decoding; the other two peak at lower loads because added sessions turn into recompute.

## References

- [llm-d guides](https://github.com/llm-d/llm-d/tree/main/guides): agentic-serving, tiered-prefix-cache, p2p-kv-cache-sharing, optimized-baseline, pd-disaggregation; [peak prefill throughput calibration](https://github.com/llm-d/llm-d/tree/main/guides/recipes/router/calibration)
- [llm-d-router](https://github.com/llm-d/llm-d-router): prefix-cache-affinity-filter, token-load-scorer, precise-prefix-cache-producer, p2p-source-producer
- [Reproduction kit for this post](https://github.com/kaushikmitr/llm-d/tree/kv-cache-serving-efficiency-blog/docs/blog/kv-cache-serving-efficiency/reproduce): deployment manifests, calibration jobs, harness, analysis scripts and results
- [inference-perf](https://github.com/kubernetes-sigs/inference-perf), the load generator
- Van Drie, L., *LLM Admission and Routing: A Step-by-Step Derivation from the Spacetime Trapezoid*, derivation companion, 2026
- [NVIDIA Dynamo KV cache aware routing](https://docs.nvidia.com/dynamo/user-guides/kv-cache-aware-routing)

## Appendix A. Inputs and calibration

| Symbol | Meaning | How it is obtained |
| --- | --- | --- |
| F\_peak | dense accelerator FLOP/s per replica | datasheet |
| α\_F | prefill efficiency, achieved over peak | calibration job: peak prefill throughput × 2N / F\_peak |
| W\_peak | HBM bandwidth per replica | datasheet |
| α\_W | decode read efficiency | calibration job: marginal KV-read bandwidth between one and four long-context decodes, over W\_peak |
| C | HBM bytes available for KV | engine startup log |
| B, T\_0 | effective inter-node link for KV, and setup latency | fitted to the llm-d P2P ladder (gpt-oss-120b on H200, RDMA): 17.3 GB/s, 26 ms |
| N | active parameters | model card |
| b | KV bytes per token, 2 · layers · KV heads · head dim · bytes | model shape, or host-tier bytes loaded over host-tier hits |
| W\_b | weight bytes a step reads | model card; for a mixture of experts, the experts the step's tokens touch (Appendix B) |
| I, O | token-weighted mean input and output length | the trace, with the output length's coefficient of variation |
| ρ | intrinsic overlap | an oracle cache with unlimited retention replayed on the trace |
| c | offered concurrency per replica | the load generator, closed-loop runs only |

F = α\_F F\_peak and W = α\_W W\_peak. The two efficiency factors are the only numbers not on a datasheet or a model card, and each is one short job on one replica: the llm-d router's calibration script measures peak prefill throughput as chunk size over median time to first token for a cache-missing prompt, and a decode-only job at one long context measures the KV read rate from the step-time difference between one and four decodes (decode\_calib.py in the reproduction kit). The measured roofs of Section 2 are these two numbers: the prefill rate at a 128k context, 22,794 tokens per second, converted to FLOP/s, and 4.2 TB/s per GPU, 53% of the datasheet's 8 TB/s. The compute roof is evaluated near the runs' mean context (110k to 124k tokens); prompts range from about 34k at p10 to 251k at p99, and per-token prefill slows as context grows, so a roof weighted by the actual mix would sit a few percent lower.

The B200 deployment the comparison in Section 6 ran on was calibrated on 11 October 2026: one replica of Qwen3-Coder-480B-A35B-FP8 at TP=8 with expert parallelism, same flags as the fleet (8k chunk, 64 sequences, 0.9 memory utilisation, 64-token blocks), on 8 × B200 with vLLM v0.28.0, the version Dynamo v1.5.0 pins and therefore the engine both stacks ran. An 8 × H200 replica with vLLM v0.27.1 was calibrated the same way the day before and is kept for reference; the B200 numbers on v0.27.1 were within 2% of these on every row.

| Quantity | 8 × H200, vLLM v0.27.1 | 8 × B200, vLLM v0.28.0 | How |
| --- | --- | --- | --- |
| Peak prefill throughput, 8k / 32k / 128k prompt | 26,701 / 22,763 / 13,095 tok/s | 37,217 / 33,631 / 22,794 tok/s | calibration job, fresh random token-ID prompts, median of 15 (8k) or 8 |
| α\_F at 8k, and falloff with context | 0.24 of dense bf16 peak; (I/8192)^−0.26 | 0.15 of dense bf16 peak; (I/8192)^−0.18 | from the three points above |
| C | 2,209,344 tokens, about 535 GiB | 3,403,712 tokens, about 805 GiB | engine startup log, bf16 KV |
| α\_W | 0.70 of 38.4 TB/s | 0.53 of 64 TB/s | marginal KV-read bandwidth from the step-time increase between 1 and 4 concurrent 131k-token decodes |
| Step-time floor, one short sequence | 7.9 ms | 5.8 ms | launch and collective overhead: a mixture-of-experts step reads only the experts it touches, about 35 GB of 480 at one sequence |
| Prefill interference | a decode step sharing its iteration with an 8k chunk takes about 310 ms (10 ms alone) | about 220 ms (7.6 ms alone) | 8 concurrent 131k-token requests; median TPOT 400 ms on H200, 259 ms on B200 while the others prefilled |

## Appendix B. The step-time law and the ridge

This appendix derives the ceiling R used throughout from the three costs of a step described in Section 3, with the symbols of Section 3 and the calibrated inputs of Appendix A.

### B.1 The step-time law

Engines run continuous batching with chunked prefill. One scheduler iteration holds up to T\_b tokens of prefill chunks plus one token for every sequence in decode, and runs them as one forward pass. Prefill and decode are not two pipelines with separate bounds: they share one iteration, and a prefill chunk lengthens the step for every decode sequence in it. The iteration is therefore the unit. With n\_p prefill tokens and n\_d decode tokens in the iteration, its duration is the larger of its compute time and its memory time, and Little's law ties the counts to the request rate λ per replica at hit rate h:

```latex
t_{iter} = \max\left( \frac{2N\,(n_p + n_d)}{F},\; \frac{W_b + n_d\, b\, \bar{c} + \frac{n_p}{T_b}\, b\, I}{W} \right), \quad n_p = \lambda I (1-h)\, t_{iter}, \quad n_d = \lambda O\, t_{iter}, \quad \bar{c} = I + \tfrac{O}{2}
```

The memory term reads the weights once, the KV of every decode sequence once (each decode token attends over its whole context), and the prefix once per prefill chunk. c̄ is the time-average resident context: a request's KV traces a trapezoid in memory and time, from I at arrival to I + O at exit. A rate is sustainable only while all four of the following hold, and the first to fail as λ rises is the binding resource:

```latex
\text{compute: } 2N\lambda\,(I(1-h)+O) < F \qquad \text{bandwidth: } \lambda\, b\,(O\bar{c} + I(1-h)\tfrac{I}{T_b}) < W \qquad \text{capacity: } n_d\, b\, \bar{c} \le C \qquad \text{budget: } n_p \le T_b
```

Each condition bounds λ, the request rate one replica sustains, on its own, and three of them in closed form (the prefill-budget condition never binds at an 8k chunk). With U = (1 − ρ)·I and L = I + O/2, and dividing the replica's token rate by the accelerators it spans (8 GPUs at TP=8) to get the per-accelerator R:

```latex
\lambda_c = \frac{F}{2N\,(U + O)}, \qquad \lambda_w = \frac{W}{b\,(O L + U I / T_b)}, \qquad \lambda_m = \frac{C}{\frac{W_b}{W}\left(O L b + \frac{U I b}{2 T_b}\right) + C\,\frac{b\,(O L + U I / T_b)}{W}}, \qquad R = \frac{(I + O)\,\min(\lambda_c, \lambda_w, \lambda_m)}{\text{accelerators per replica}}
```

For a dense model that is the whole computation. For a mixture-of-experts model W\_b depends on how many tokens share a step, so it is evaluated at the tokens the solution implies: the n = λ\_m·O·t\_iter decoding tokens plus the n·U/O prefill tokens spread into the step, and the pair converges in two or three iterations. For the B200 fleet, λ\_m = 1.89 requests per second per replica with W\_b = 480 GB at 29 resident sequences and about 540 prefill tokens per step, which is the 27.6k tokens per second per GPU used throughout.

The roofline R(I, O, h) is the largest λ that satisfies all four, times (I + O), per accelerator. Time per output token is t\_iter, and time to first token is the queue wait plus I(1−h)/T\_b iterations. Neither is a resource; both are what the resources are spent against, and where a time-per-output-token target applies, R is evaluated at the concurrency that target admits. Two corrections to carry. With a distribution of output lengths the resident context is I + ½Ō(1 + CV²), because a snapshot of the batch catches long generations in proportion to their length, so at CV² = 1 the decode share doubles. Writing the iteration as a sum instead of a max is the conservative form that bounds an engine overlapping nothing; measured inter-token latencies in the llm-d guides sit nearer the max. For a mixture-of-experts model the weight term W\_b is not constant: a step reads only the experts its tokens route to, so with E experts and k active per token the bytes read grow with the number of tokens in the step, decode and prefill alike, as the dense weights plus E(1 − (1 − k/E)^n)·e expert weights, from about 35 GB at one token to 374 GB at 29 and nearly all 480 GB once a step carries a few hundred tokens, for Qwen3-Coder-480B. The ceilings used here take that form; the small-batch floor is then launch and collective overhead rather than bandwidth.

### B.2 The ridge

Per active request, with U uncached prompt tokens, resident context L and output O (Section 3), each iteration costs compute for its amortised uncached prefill and its decode token, and a read of its resident context:

```latex
c = \frac{1 + U/O}{R_{pf}}, \qquad m = \frac{L}{BW}, \qquad R_{pf} = \frac{F}{2N}, \quad BW = \frac{W}{b}
```

These are the terms that grow with load. The weight stream W\_b/W is paid once per iteration whatever the load, so at low load every decode step is dominated by it and per-token cost looks memory-bound, the familiar small-batch result. Compute-bound and memory-bound are used here for the ceiling, where the per-sequence terms have grown to their limit, and that classification does not depend on load. Compute binds there when c exceeds m: when the KV tokens scanned per processed token, L/(1 + U/O), is below the hardware's ridge r = BW/R\_pf. The weight stream is kept by charging it per sequence: a step shared by n sequences reads W\_b(n) once, which is W\_b/(n·b) tokens of KV each, so the comparison is made with L\_eff = L + W\_b(n)/(n·b) in place of L, and Section 2.1 draws it for several n. The hit rate appears nowhere but inside U. Solving the equality for O gives the boundary output length as a function of context and uncached prefill,

```latex
O^{*}(L, U) = \frac{r\, U}{L - r} \quad (L > r), \qquad r = \frac{BW}{R_{pf}}
```

Contexts shorter than the ridge r are compute-bound at any output length. Longer than r, memory binds once the output exceeds rU/(L − r). Rearranged, compute binds when U/O > L/r − 1: the uncached input per output token must exceed the context in ridges, less one. With nothing cached (U = L = I) this is 1/I + 1/O > 1/r, which for a long input reduces to O < r: on the B200 deployment memory binds above about 3,600 output tokens. With everything cached (U = 0) the boundary is zero: a reused context longer than r is memory-bound whatever its output length, which is where the agentic workload of Section 6 sits. The ridge is the hardware's bytes per FLOP times the model's FLOPs per token over its KV bytes per token, so latent-attention models, with fewer KV bytes per token, land higher.
