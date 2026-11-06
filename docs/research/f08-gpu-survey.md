# F-08 team GPU survey

**Bottom line:** 9 of 10 teammates answered. Five machines have an NVIDIA GPU with 8 GB or less, two can't run GPU models (integrated graphics, and an 8 GB Apple M2), and **two are RTX 5070s, Alex's and Nickolas's** (VRAM not confirmed for either). That backs D-008: most of the team builds on the `stub` backend and on cached results computed on a 5070.

Asked in the team chat on 2026-10-28. The questions were (1) GPU and VRAM, (2) OS, (3) other compute or credits you can use. Answers are as reported. Nobody has run `python -m vibe.modules doctor` on them yet, except where the "Checked" column says so.

## Answers

| Teammate | Answered | GPU / memory (as reported) | OS | Other compute or credits | Checked |
|---|---|---|---|---|---|
| Jonathan S. | 2026-10-28 | RTX 5060 Ti, 8 GB | Windows | FIU quantum computer, from 2027 | Not yet |
| Chris | 2026-10-28 | Integrated graphics | Windows 11 | $400 Snowflake credits, $30 Gemini API credits | Not yet |
| Pablo H. | 2026-10-28 | Apple M5 Pro (18-core CPU, 16-core GPU), 64 GB unified memory | macOS | 100 Claude API credits | Not yet |
| Paul A. | 2026-10-31 | RTX 3050, 4 GB | Windows 11 | None | Not yet |
| Alex W. | 2026-10-31 | **No answer in the chat.** RTX 5070, the same card as Nickolas's (Eduardo, 2026-11-05) | Not given | | Not yet |
| Luis P. | 2026-10-31 | RTX 4050 Laptop, 6 GB | Windows 11 | HiPerGator (UF) | Driver 555.97, from his F-10 handoff |
| Nickolas G. | 2026-10-31 | Reported "RTX Blackwell 200 16gb"; **corrected to RTX 5070** (Eduardo, 2026-11-05) | Windows 11 | MDC Makers Space | Not yet |
| Paul P. | 2026-11-01 | RTX 4060 Ti, 8 GB | Windows 11 | None given | `nvidia-smi` 2026-11-01: 8188 MiB, driver 610.60 (R-1) |
| Jean M. | 2026-11-01 | RTX 3050 Laptop, 4 GB, driver 592 | Windows | None | Driver as reported |
| Eduardo G. | 2026-11-05 | Apple M2, 8 GB unified memory | macOS 26.6.2 | | `sysctl` on the machine |

- **Jonathan's row is inferred.** The first answer had no name. It matches the GPU and the FIU quantum computer in R-1's table for Jonathan ([r1-planner-llm.md](r1-planner-llm.md#team-hardware-and-where-each-local-model-runs)).
- **Alex's and Nickolas's cards are both RTX 5070s,** per Eduardo on 2026-11-05. That replaces Nickolas's chat answer and R-1's table, which list an RTX PRO 2000 Blackwell. **The 16 GB Nickolas reported doesn't match a 5070**, which has 12 GB on the desktop card and 8 GB on the laptop version. So treat both VRAMs as unconfirmed until `doctor` runs on each machine.

## What each machine can do

Tiers are those in [ARCHITECTURE "Compute"](../ARCHITECTURE.md#compute-everyone-can-work-whatever-their-machine): **0** stub backend and CPU-sized models only, **1** small or quantized models one at a time (~4–8 GB), **2** full models and benchmarks. The tiers come from the reported memory using the same rule as `doctor` (12 GB+ NVIDIA is tier 2, and Apple Silicon needs 16 GB+ for tier 1). They're for planning who runs what, not measurements.

| Teammate | Tier | Needs CUDA 12.8 (driver 570+) | Notes |
|---|---|---|---|
| Alex W. | 2 if 12 GB, 1 if 8 GB | Yes (Blackwell) | The reference machine in D-008. Confirm the VRAM |
| Nickolas G. | 2 if 12 GB, 1 if 8 GB | Yes (Blackwell) | Same card as Alex's, so a second 5070 for benchmarks. Confirm the VRAM |
| Pablo H. | 1 (Metal) | No CUDA | 64 GB fits big models, but CUDA-only libraries (TensorRT, custom kernels) won't run. Speeds aren't comparable to NVIDIA |
| Jonathan S. | 1 | Yes (Blackwell) | |
| Paul P. | 1 | No (Ada); driver 610 is fine for `cu128` | |
| Luis P. | 1 | No (Ada), but **driver 555.97 is too old for `cu128`**: update to 570+ | |
| Paul A. | 1, tight | No (Ampere) | 4 GB: one small model at a time |
| Jean M. | 1, tight | No (Ampere); driver 592 is fine | 4 GB |
| Eduardo G. | 0 | No CUDA | 8 GB unified memory is shared with the OS |
| Chris | 0 | No | |

## What follows from it

- **Alex and Nickolas need to confirm their VRAM.** Each runs `uv run --directory backend python -m vibe.modules doctor` and posts the output. It decides whether a 5070 fits D-008's "detector + tracker + identity together" for live mode (F-50).
- **Two 5070s mean two reference machines.** D-008's reference stays the RTX 5070, and benchmark numbers from either machine are comparable if both are the same variant (desktop or laptop) and run the same driver and PyTorch build. Say which machine in each report.
- **GPU work goes to:** Alex and Nickolas for benchmarks, F-50 and real end-to-end runs. Pablo can do Metal-only spot checks. Jonathan, Paul P. and Luis can develop one model at a time. Everyone else works on `stub` plus a copied `cache/<sha256>/`.
- **Three cards are Blackwell** (two 5070s and the 5060 Ti), so they need the `cu128` extra (PyTorch 2.7+). See the README's backend setup.
- **Other compute:** HiPerGator (Luis) and the MDC Makers Space (Nickolas) are options for tier 3 jobs. Under D-017 they count as third-party unless the team controls the machine, so footage needs the project opt-in. Pablo's Claude credits and Chris's Gemini credits cover hosted-LLM runs (R-1).

## Updating this

When someone runs `doctor` on their machine, replace their row's "as reported" values and fill in "Checked" with the date and the driver.
