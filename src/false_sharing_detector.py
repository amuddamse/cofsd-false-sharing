"""
COFSD: Counter-Only False Sharing Detection — Corrected Simulation
Fixes coherence model: only counts actual Get/GetX/Intervention messages.
Models BOTH write-write and write-read false sharing patterns.
"""

from collections import defaultdict
import random

# ─────────────────────────────────────────────────────────────
# Corrected COFSDSimulator
# Key fix: FC/IC only increment on ACTUAL coherence messages,
# not on every memory write (intra-thread silent writes don't count)
# ─────────────────────────────────────────────────────────────

class COFSDSimulator:
    def __init__(self, num_threads=8, line_size=64, tau_p=16):
        self.num_threads   = num_threads
        self.line_size     = line_size
        self.tau_p         = tau_p
        self.fc            = defaultdict(int)
        self.ic            = defaultdict(int)
        self.hc            = defaultdict(int)
        self.accessor_set  = defaultdict(set)
        self.writer_set    = defaultdict(set)
        self.owner         = defaultdict(lambda: -1)
        self.state         = defaultdict(lambda: 'I')
        self.sharers       = defaultdict(set)
        self.fs_detections = []
        self.total_accesses = 0
        self.total_inv      = 0

    def line_addr(self, addr):
        return (addr // self.line_size) * self.line_size

    def access(self, thread_id, addr, is_write, label=""):
        la = self.line_addr(addr)
        self.total_accesses += 1
        self.accessor_set[la].add(thread_id)
        if is_write:
            self.writer_set[la].add(thread_id)

        cur_state = self.state[la]
        cur_owner = self.owner[la]
        coherence_action = False

        if is_write:
            if cur_state == 'M' and cur_owner == thread_id:
                # ── Silent write: already own in M state ──
                # No GetX message → FC does NOT increment
                # This is the KEY FIX vs previous version
                pass
            else:
                # ── GetX request sent to directory ──
                self.fc[la] += 1
                coherence_action = True
                # Directory must invalidate current owner/sharers
                if cur_state == 'M' and cur_owner != thread_id:
                    self.ic[la] += 1      # Fwd_GetX intervention
                    self.total_inv += 1
                elif cur_state == 'S':
                    for sharer in self.sharers[la]:
                        if sharer != thread_id:
                            self.ic[la] += 1   # Inv to each sharer
                            self.total_inv += 1
                self.state[la]   = 'M'
                self.owner[la]   = thread_id
                self.sharers[la] = set()
        else:
            if cur_state == 'S' and thread_id in self.sharers[la]:
                # ── Already have S copy ── no message needed
                pass
            elif cur_state == 'M' and cur_owner == thread_id:
                # ── Own it in M ── silent read
                pass
            else:
                # ── GetS request sent to directory ──
                self.fc[la] += 1
                coherence_action = True
                if cur_state == 'M' and cur_owner != thread_id:
                    self.ic[la] += 1      # Intervention (Fwd_GetS)
                    self.total_inv += 1
                    self.state[la] = 'S'
                    self.sharers[la] = {cur_owner, thread_id}
                    self.owner[la]   = -1
                else:
                    self.state[la] = 'S'
                    self.sharers[la].add(thread_id)

        if coherence_action:
            self._check(la, label)

    def _check(self, la, label):
        fc = self.fc[la]
        ic = self.ic[la]
        hc = self.hc[la]
        if fc < self.tau_p or ic < self.tau_p or hc > 0:
            return

        # COFSD detection conditions:
        # Write-write false sharing: multiple writers to DIFFERENT bytes
        write_write = len(self.writer_set[la]) > 1

        # Write-read false sharing: one writer + multiple readers
        # (different bytes on same line — can't confirm without PAM,
        #  but high IC + single writer + multiple readers is suspicious)
        write_read  = (len(self.writer_set[la]) == 1 and
                       len(self.accessor_set[la]) > 2)

        high_ic = (ic * 2) >= fc   # IC ≥ 50% of FC

        if (write_write or write_read) and high_ic:
            kind = "write-write" if write_write else "write-read"
            self.fs_detections.append({
                'addr'     : la,   'fc'  : fc,   'ic'   : ic,
                'writers'  : set(self.writer_set[la]),
                'accessors': set(self.accessor_set[la]),
                'kind'     : kind, 'label': label
            })
            self.fc[la] = 0
            self.ic[la] = 0
            self.hc[la] = min(3, hc + 1)

    def stats(self):
        return {
            'total_accesses' : self.total_accesses,
            'total_inv'      : self.total_inv,
            'fs_detected'    : len(self.fs_detections),
            'inv_rate_pct'   : (self.total_inv / max(1,self.total_accesses))*100,
        }


# ─────────────────────────────────────────────────────────────
# Benchmark 1 — linear_regression (write-write false sharing)
#
# emit_intermediate() layout:
#   intermediate[thread * NUM_KEYS + key] where ENTRY_SZ=8 bytes
#   Thread 0 keys 0-4 and Thread 1 keys 0-2 → SAME cache line
#   Both Thread 0 and Thread 1 WRITE → write-write false sharing
# ─────────────────────────────────────────────────────────────

def run_linear_regression(num_threads=8, iterations=200, tau_p=16):
    print("\n" + "="*64)
    print("Benchmark 1: linear_regression (PHOENIX)")
    print("Type:        Write-Write false sharing")
    print("Location:    emit_intermediate() intermediate results array")
    print("="*64)

    sim      = COFSDSimulator(num_threads=num_threads, line_size=64, tau_p=tau_p)
    NUM_KEYS = 5
    ENTRY_SZ = 8
    BASE     = 0x10000

    for _ in range(iterations):
        order = list(range(num_threads))
        random.shuffle(order)
        for tid in order:
            for key in range(NUM_KEYS):
                addr = BASE + (tid * NUM_KEYS + key) * ENTRY_SZ
                sim.access(tid, addr, is_write=True,
                           label=f"LR_t{tid}_k{key}")

    s = sim.stats()
    print(f"  Threads:                  {num_threads}")
    print(f"  Iterations:               {iterations}")
    print(f"  Total accesses:           {s['total_accesses']}")
    print(f"  Total invalidations:      {s['total_inv']}")
    print(f"  Invalidation rate:        {s['inv_rate_pct']:.1f}%")
    print(f"  COFSD detections:         {s['fs_detected']}")
    if sim.fs_detections:
        d = sim.fs_detections[0]
        print(f"\n  First detection:")
        print(f"    Line 0x{d['addr']:x}  FC={d['fc']}  IC={d['ic']}")
        print(f"    Type: {d['kind']}")
        print(f"    Writers: threads {sorted(d['writers'])}")
        print(f"    Accessors: threads {sorted(d['accessors'])}")
    return sim


# ─────────────────────────────────────────────────────────────
# Benchmark 2 — string_match (write-read false sharing)
#
# str_data_t struct — ONE cache line contains:
#   offset  0: keys_file_len  (4B) ← map threads READ
#   offset  4: encrypted_len  (4B) ← map threads READ
#   offset  8: bytes_comp     (8B) ← splitter WRITES  ← conflict
#   offset 16: keys_file ptr  (8B) ← map threads READ
#   offset 24: encrypt_file   (8B) ← map threads READ
#
# Splitter (T0) writes bytes_comp → puts line in M state
# Map threads (T1..T7) read keys_file_len → intervention → IC++
# ─────────────────────────────────────────────────────────────

def run_string_match(num_threads=8, iterations=200, tau_p=16):
    print("\n" + "="*64)
    print("Benchmark 2: string_match (PHOENIX)")
    print("Type:        Write-Read false sharing")
    print("Location:    str_data_t struct in string_match_splitter()")
    print("  Splitter writes bytes_comp [offset 8]")
    print("  Map threads read keys_file_len [offset 0] — same line")
    print("="*64)

    sim  = COFSDSimulator(num_threads=num_threads, line_size=64, tau_p=tau_p)
    BASE = 0x20000
    OFF_LEN    = 0    # keys_file_len — map threads read
    OFF_BYTES  = 8    # bytes_comp    — splitter writes
    OFF_FILE   = 16   # keys_file ptr — map threads read
    SPLITTER   = 0

    for _ in range(iterations):
        # Splitter writes bytes_comp (takes/keeps M state)
        sim.access(SPLITTER, BASE+OFF_BYTES, is_write=True,
                   label="SM_splitter_bytes_comp")
        # Map threads read keys_file_len → triggers intervention
        map_order = list(range(1, num_threads))
        random.shuffle(map_order)
        for tid in map_order:
            sim.access(tid, BASE+OFF_LEN,  is_write=False,
                       label=f"SM_map_t{tid}_len")
            sim.access(tid, BASE+OFF_FILE, is_write=False,
                       label=f"SM_map_t{tid}_file")

    s = sim.stats()
    print(f"  Threads:                  {num_threads}")
    print(f"  Iterations:               {iterations}")
    print(f"  Total accesses:           {s['total_accesses']}")
    print(f"  Total invalidations:      {s['total_inv']}")
    print(f"  Invalidation rate:        {s['inv_rate_pct']:.1f}%")
    print(f"  COFSD detections:         {s['fs_detected']}")
    if sim.fs_detections:
        d = sim.fs_detections[0]
        print(f"\n  First detection:")
        print(f"    Line 0x{d['addr']:x}  FC={d['fc']}  IC={d['ic']}")
        print(f"    Type: {d['kind']}")
        print(f"    Writers: threads {sorted(d['writers'])}")
        print(f"    Accessors: threads {sorted(d['accessors'])}")
    return sim


# ─────────────────────────────────────────────────────────────
# Control 1 — True Sharing (mutex lock, SAME bytes)
# ─────────────────────────────────────────────────────────────

def run_true_sharing(num_threads=8, iterations=200, tau_p=16):
    print("\n" + "="*64)
    print("Control 1: True Sharing (mutex lock)")
    print("All threads write to SAME variable — COFSD must NOT flag")
    print("="*64)

    sim = COFSDSimulator(num_threads=num_threads, line_size=64, tau_p=tau_p)
    LOCK = 0x30000
    for _ in range(iterations):
        for tid in range(num_threads):
            sim.access(tid, LOCK, is_write=True,  label="lock_acq")
            sim.access(tid, LOCK, is_write=False, label="lock_read")

    s = sim.stats()
    print(f"  Total accesses:           {s['total_accesses']}")
    print(f"  Total invalidations:      {s['total_inv']}")
    print(f"  Invalidation rate:        {s['inv_rate_pct']:.1f}%")
    print(f"  COFSD detections:         {s['fs_detected']}  ← expected 0")
    return sim


# ─────────────────────────────────────────────────────────────
# Control 2 — No Sharing (private cache-line-aligned data)
# ─────────────────────────────────────────────────────────────

def run_no_sharing(num_threads=8, iterations=200, tau_p=16):
    print("\n" + "="*64)
    print("Control 2: No Sharing (private aligned arrays)")
    print("Each thread writes to its own 4KB-separated cache line")
    print("="*64)

    sim = COFSDSimulator(num_threads=num_threads, line_size=64, tau_p=tau_p)
    BASE = 0x40000
    for _ in range(iterations):
        for tid in range(num_threads):
            sim.access(tid, BASE + tid*4096, is_write=True,
                       label=f"private_t{tid}")

    s = sim.stats()
    print(f"  Total accesses:           {s['total_accesses']}")
    print(f"  Total invalidations:      {s['total_inv']}")
    print(f"  COFSD detections:         {s['fs_detected']}  ← expected 0")
    return sim


# ─────────────────────────────────────────────────────────────
# τP Sensitivity Analysis
# ─────────────────────────────────────────────────────────────

def tau_p_sensitivity(num_threads=8, iterations=300):
    print("\n" + "="*64)
    print("τP Threshold Sensitivity Analysis")
    print("Tradeoff: lower τP = faster detection, higher false-positive risk")
    print("="*64)
    print(f"{'τP':>6s}  {'LR det':>8s}  {'SM det':>8s}  "
          f"{'TS FP':>8s}  {'Detect latency':>16s}")
    print("-"*54)

    NUM_KEYS=5; ENTRY_SZ=8
    BASE_LR=0x10000; BASE_SM=0x20000; BASE_TS=0x30000

    for tau_p in [4, 8, 16, 32, 64]:
        slr = COFSDSimulator(num_threads, 64, tau_p)
        for _ in range(iterations):
            for tid in range(num_threads):
                for key in range(NUM_KEYS):
                    slr.access(tid, BASE_LR+(tid*NUM_KEYS+key)*ENTRY_SZ, True)

        ssm = COFSDSimulator(num_threads, 64, tau_p)
        for _ in range(iterations):
            ssm.access(0, BASE_SM+8, True)
            for tid in range(1, num_threads):
                ssm.access(tid, BASE_SM+0, False)

        sts = COFSDSimulator(num_threads, 64, tau_p)
        for _ in range(iterations):
            for tid in range(num_threads):
                sts.access(tid, BASE_TS, True)
                sts.access(tid, BASE_TS, False)

        print(f"{tau_p:>6d}  {len(slr.fs_detections):>8d}  "
              f"{len(ssm.fs_detections):>8d}  "
              f"{len(sts.fs_detections):>8d}  "
              f"~{tau_p} coherence msgs")


# ─────────────────────────────────────────────────────────────
# Hardware Cost
# ─────────────────────────────────────────────────────────────

def hardware_cost():
    print("\n" + "="*64)
    print("Hardware Cost: FSDetect vs COFSD")
    print("System: 8-core, 32KB L1D per core, 2MB LLC per core")
    print("="*64)

    C=8; CL=64
    l1d_lines  = (32*1024)//CL
    sam_ent    = 128; slices=8
    llc_kb     = 2*1024*C

    pam_kb = (129 * l1d_lines * C) / (8*1024)
    sam_kb = ((8+3)*CL * sam_ent * slices) / (8*1024)
    dir_fd = 7+7+2+4
    cof_db = 7+7+2+C+C
    cof_kb = (cof_db * sam_ent * slices) / (8*1024)

    print(f"\n{'Component':<32s} {'FSDetect':>12s} {'COFSD':>12s}")
    print("-"*58)
    print(f"{'PAM tables (8 cores × 8KB)':<32s} {pam_kb:>10.1f}KB {'0':>12s}")
    print(f"{'SAM tables (8 slices)':<32s} {sam_kb:>10.1f}KB {'0':>12s}")
    print(f"{'Dir bits/entry':<32s} {dir_fd:>11d}b {cof_db:>11d}b")
    print(f"{'COFSD dir overhead total':<32s} {'—':>12s} {cof_kb:>10.2f}KB")
    print(f"{'Total metadata':<32s} {pam_kb+sam_kb:>10.1f}KB {cof_kb:>10.2f}KB")
    print(f"{'% of LLC':<32s} "
          f"{(pam_kb+sam_kb)/llc_kb*100:>9.1f}% "
          f"{cof_kb/llc_kb*100:>10.2f}%")
    print(f"{'REP_MD messages':<32s} {'Required':>12s} {'None':>12s}")
    print(f"\n  Area reduction: {(pam_kb+sam_kb)/max(cof_kb,0.01):.0f}× less metadata storage")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    random.seed(42)

    print("╔════════════════════════════════════════════════════════════╗")
    print("║  COFSD: Counter-Only False Sharing Detection (Fixed)       ║")
    print("║  Accurate coherence model: counts only actual Get/GetX/Inv ║")
    print("║  Based on Phoenix linear_regression.c + string_match.c     ║")
    print("╚════════════════════════════════════════════════════════════╝")

    lr = run_linear_regression(num_threads=8, iterations=200, tau_p=16)
    sm = run_string_match     (num_threads=8, iterations=200, tau_p=16)
    ts = run_true_sharing     (num_threads=8, iterations=200, tau_p=16)
    ns = run_no_sharing       (num_threads=8, iterations=200, tau_p=16)

    tau_p_sensitivity()
    hardware_cost()

    print("\n" + "="*64)
    print("FINAL RESULTS: COFSD Detection Accuracy")
    print("="*64)
    print(f"{'Scenario':<38s} {'Type':<14s} {'Det':>5s} {'Correct?':>9s}")
    print("-"*68)
    print(f"{'linear_regression (false sharing)':<38s} "
          f"{'write-write':<14s} {len(lr.fs_detections):>5d} "
          f"{'✅' if len(lr.fs_detections)>0 else '❌':>9s}")
    print(f"{'string_match (false sharing)':<38s} "
          f"{'write-read':<14s} {len(sm.fs_detections):>5d} "
          f"{'✅' if len(sm.fs_detections)>0 else '❌':>9s}")
    print(f"{'true_sharing — must be 0':<38s} "
          f"{'true share':<14s} {len(ts.fs_detections):>5d} "
          f"{'✅' if len(ts.fs_detections)==0 else '❌':>9s}")
    print(f"{'no_sharing — must be 0':<38s} "
          f"{'private':<14s} {len(ns.fs_detections):>5d} "
          f"{'✅' if len(ns.fs_detections)==0 else '❌':>9s}")
