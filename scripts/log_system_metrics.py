import os
import time
import csv
import argparse
import psutil

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--outdir", type=str, required=True)
    p.add_argument("--interval", type=float, default=1.0)
    args = p.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    outpath = os.path.join(args.outdir, "system_metrics.csv")

    proc = psutil.Process(args.pid)

    # Prime CPU% measurement (psutil requires an initial call)
    proc.cpu_percent(interval=None)

    with open(outpath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_wall", "cpu_percent", "rss_bytes", "vms_bytes", "num_threads"])
        f.flush()

        while True:
            # Sleep first so every sample, including the first, covers a full
            # interval since the previous cpu_percent() call. (Sampling right
            # after the priming call gave a microsecond window: 0% or a
            # spurious value of thousands of %.)
            time.sleep(args.interval)
            if not proc.is_running():
                break
            try:
                ts = time.time()
                cpu = proc.cpu_percent(interval=None)
                mem = proc.memory_info()
                w.writerow([ts, cpu, mem.rss, mem.vms, proc.num_threads()])
                f.flush()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                break

if __name__ == "__main__":
    main()

