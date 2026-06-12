# COFSD: Counter-Only False Sharing Detection
Reducing the Hardware Cost of On-the-fly False-Sharing Detection in Cache-Coherent Multicores

**CSE 220 — Computer Architecture Final Project**
**UC Santa Cruz, Baskin School of Engineering — June 2026**
**Author**: Anil Muddamsetti

---

## Overview

COFSD proposes replacing the 152.5 KB PAM/SAM metadata tables in FSDetect (MICRO 2024) with 32 bits per directory entry: FC + IC + HC + AccessorSet + WriterSet bitmasks — achieving 38× less hardware overhead while correctly detecting false sharing in Phoenix benchmarks.

Based on: *"Leveraging Cache Coherence to Detect and Repair False Sharing On-the-fly"* (MICRO 2024, Patel et al., IIT Kanpur)

---

## Repository Structure
src/
false_sharing_detector.py   # COFSD Python MESI coherence simulator

results/
results.txt                 # Full simulation output

benchmarks/phoenix/
linear_regression.c         # Phoenix benchmark: write-write false sharing
string_match.c              # Phoenix benchmark: write-read false sharing

report/
COFSD_Final_Report.pdf      # Project report

slides/
COFSD_Final_Project.pptx    # Presentation slides

---

## How to Run

**Requirements**: Python 3.x (standard library only, no additional packages needed)


%cd src ;
%python3 false_sharing_detector.py


#Expected output is saved in `results/results.txt`.

---

## Key Results

| Scenario | Type | Detected | Correct? |
|---|---|---|---|
| linear_regression | Write-write false sharing | 5 | ✅ |
| string_match | Write-read false sharing | 1 | ✅ |
| True sharing (lock) | Control : must be 0 | 0 | ✅ |
| No sharing (private) | Control : must be 0 | 0 | ✅ |

### Hardware Cost Comparison (8-core system)

| Metric | FSDetect | COFSD |
|---|---|---|
| PAM tables | 64.5 KB | 0 KB |
| SAM tables | 88.0 KB | 0 KB |
| Total metadata | 152.5 KB (0.9% LLC) | 4.0 KB (0.02% LLC) |
| REP_MD messages | Required | Eliminated |
| Area reduction | baseline | **38x** |

---

## False Sharing Patterns Modeled

### linear_regression.c : Write-Write False Sharing
emit_intermediate(): writes 5 key-value pairs per thread into a shared
intermediate results array. Memory layout:
intermediate[thread_id * NUM_KEYS + key]  (entry size = 8 bytes)
Thread 0 keys 0-4 and Thread 1 keys 0-2 → SAME 64-byte cache line
All 8 threads write → write-write false sharing

### string_match.c — Write-Read False Sharing
str_data_t: struct fields share a single cache line:

#c
typedef struct {
  int  keys_file_len;      // offset 0  ← map threads READ
  int  encrypted_file_len; // offset 4  ← map threads READ
  long bytes_comp;         // offset 8  ← splitter WRITES  ← conflict
  char *keys_file;         // offset 16 ← map threads READ
  char *encrypt_file;      // offset 24 ← map threads READ
} str_data_t;

Splitter writes "bytes_comp", map threads read adjacent fields
on the same 64-byte cache line → write-read false sharing.

---

## COFSD Design

### Directory Entry (32 bits)
FC[7] | IC[7] | HC[2] | AccessorSet[8] | WriterSet[8]

### Detection Condition
FC ≥ τP  AND  IC ≥ τP  AND  IC/FC ≥ 50%  AND
( WriterSet > 1          ← write-write false sharing  OR
WriterSet = 1 AND AccessorSet > 2  ← write-read false sharing )

Where τP = 16 (privatization threshold)

### Key Advantage Over FSDetect
- No PAM table per L1D cache (eliminates 64.5 KB)
- No SAM table per LLC slice (eliminates 88.0 KB)
- No REP_MD metadata messages on every intervention
- 38× less total metadata storage

### Known Limitation
One false positive class: pure write-write true sharing (e.g., mutex lock
where all threads write to the same variable). WriterSet grows to {0..7},
which is indistinguishable from write-write false sharing without byte-level
tracking. This is structurally constant regardless of τP threshold — exactly
the case FSDetect's PAM/SAM tables address.

---

## τP Sensitivity

| τP | LR detections | SM detections | TS false positives | Latency |
|---|---|---|---|---|
| 4  | 5 | 1 | 1 | ~8 acc  |
| 8  | 5 | 1 | 1 | ~16 acc |
| 16 | 5 | 1 | 1 | ~32 acc |
| 32 | 5 | 1 | 1 | ~64 acc |
| 64 | 5 | 1 | 1 | ~128 acc |

False positive is constant across all τP values — structural, not threshold-tunable.

---

## Implementation Notes

The simulator correctly models MESI coherence state transitions:
- **Silent writes**: A thread already owning a line in M state does NOT
  generate a GetX message → FC does NOT increment (key correctness fix)
- **GetX**: New exclusive request → FC++, evict owner → IC++
- **GetS with M-state line**: Intervention (Fwd_GetS) → FC++, IC++
- **GetS with S-state line**: Already shared → no coherence event

---

## Connection to CSE 220 Labs

| Lab | Topic | Connection to COFSD |
|---|---|---|
| Lab 1 | IPC / performance evaluation | COFSD overhead analysis uses IPC methodology |
| Lab 2 | 3C miss classification | False sharing manifests as conflict misses |
| Lab 3 | Victim cache | VC reduces conflict misses; COFSD targets coherence-caused conflicts |

---

## References

1.  V. Patel, S. Biswas, and M. Chaudhuri, “Leveraging Cache Coherence to Detect and Repair False Sharing On-the-fly,” in Proc. 57th IEEE/ACM Int. Symp. Microarchitecture (MICRO), 2024, pp. 823–839.
2.  V. Nagarajan, D. J. Sorin, M. D. Hill, and D. A. Wood, A Primer on Memory Consistency and Cache Coherence, 2nd ed. Morgan & Claypool, 2020.
3.  M. S. Papamarcos and J. H. Patel, “A Low-Overhead Coherence Solution for Multiprocessors with Private Cache Memories,” in Proc. Int. Symp. Computer Architecture (ISCA), 1984, pp. 348–354.
4.  C. Ranger, R. Raghuraman, A. Penmetsa, G. Bradski, and C. Kozyrakis, “Evaluating MapReduce for Multi-Core and Multiprocessor Systems,” in Proc. IEEE Int. Symp. High-Performance Computer Architecture (HPCA), 2007, pp. 13–24. Phoenix shared-memory MapReduce benchmark suite; source obtained via git clone https://github.com/kozyraki/phoenix.git (the linear_regression and string_match access patterns used in this work are derived from this suite).
5.  G. Venkataramani, C. J. Hughes, S. Kumar, and M. Prvulovic, “DeFT: Design Space Exploration for On-the-Fly Detection of Coherence Misses,” ACM Trans. Architecture and Code Optimization (TACO), vol. 8, no. 2, 2011.
6.  M. Chabbi, S. Wen, and X. Liu, “Featherlight On-the-Fly False-Sharing Detection,” in Proc. ACM SIGPLAN Symp. Principles and Practice of Parallel Programming (PPoPP), 2018, pp. 152–167.
7.  T. Liu and E. D. Berger, “SHERIFF: Precise Detection and Automatic Mitigation of False Sharing,” in Proc. ACM OOPSLA, 2011, pp. 3–18.
8.  T. A. Khan, Y. Zhao, G. Pokam, B. Mozafari, and B. Kasikci, “Huron: Hybrid False Sharing Detection and Repair,” in Proc. ACM SIGPLAN Conf. Programming Language Design and Implementation (PLDI), 2019, pp. 453–468.
9.  N. Binkert, B. Beckmann, G. Black, S. K. Reinhardt, et al., “The gem5 Simulator,” ACM SIGARCH Computer Architecture News, vol. 39, no. 2, pp. 1–7, 2011.
10.  C. Bienia, S. Kumar, J. P. Singh, and K. Li, “The PARSEC Benchmark Suite: Characterization and Architectural Implications,” in Proc. Int. Conf. Parallel Architectures and Compilation Techniques (PACT), 2008, pp. 72–81.
11.  V. Gramoli, “More Than You Ever Wanted to Know about Synchronization: Synchrobench, Measuring the Impact of the Synchronization on Concurrent Algorithms,” in Proc. PPoPP, 2015, pp. 1–10.
12.  HPS Research Group, “Scarab Microarchitectural Simulator.” [Available Online]: https://github.com/Litz-Lab/scarab
13.  HPS Research Group, “Scarab Microarchitectural Docker File.” [Available Online]: https://github.com/Litz-Lab/Scarab-infra/tree/cse220




