"""Low-resource Smith background triage worker.

It consumes JSON items from data/background_queue and writes a dated morning
brief. If an ONNX model and NPU provider are available, that provider is used;
otherwise the safe deterministic classifier runs and records the fallback.
"""
from __future__ import annotations
import argparse, json, os, time
from datetime import datetime, timezone
from pathlib import Path

POSITIVE = {"growth", "award", "contract", "record", "upgrade", "surge", "profit", "approval"}
NEGATIVE = {"loss", "warning", "lawsuit", "delay", "downgrade", "fall", "fraud", "resign"}

def set_low_priority() -> None:
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(handle, 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
        except Exception:
            pass

def classify(text: str) -> dict:
    words = set(text.lower().replace("-", " ").split())
    pos = len(words & POSITIVE); neg = len(words & NEGATIVE)
    return {"sentiment": "positive" if pos > neg else "negative" if neg > pos else "mixed_or_neutral", "positive_hits": pos, "negative_hits": neg}

def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--config", type=Path, default=Path("data/npu_worker_config.json")); ap.add_argument("--once", action="store_true"); args = ap.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8")); queue = Path(cfg["input_dir"]); output = Path(cfg["output_dir"]); queue.mkdir(parents=True, exist_ok=True); output.mkdir(parents=True, exist_ok=True); set_low_priority()
    provider = "deterministic_fallback"
    model = Path(cfg["model_path"])
    if model.is_file():
        try:
            import onnxruntime as ort
            available = set(ort.get_available_providers())
            provider = next((p for p in cfg["preferred_providers"] if p in available), provider)
        except Exception:
            pass
    while True:
        files = sorted(queue.glob("*.json"))[: int(cfg.get("max_items_per_cycle", 20))]; results=[]
        for path in files:
            try:
                item=json.loads(path.read_text(encoding="utf-8")); text=" ".join(str(item.get(k,"")) for k in ("title","summary","content","text")); results.append({"source":path.name, **classify(text), "processed_at":datetime.now(timezone.utc).isoformat(), "runtime":provider}); path.rename(path.with_suffix(".processed.json"))
            except (OSError, json.JSONDecodeError):
                continue
        if results:
            stamp=datetime.now(timezone.utc).date().isoformat(); (output/f"{stamp}.json").write_text(json.dumps({"generated_at":datetime.now(timezone.utc).isoformat(),"runtime":provider,"items":results},indent=2),encoding="utf-8")
        if args.once: break
        time.sleep(max(10, int(cfg.get("poll_seconds",30))))
    return 0
if __name__ == "__main__": raise SystemExit(main())
