# F-08 team GPU survey

**Bottom line:** 9 of 10 teammates answered. Five machines have an NVIDIA GPU with 8 GB or less, two can't run GPU models (integrated graphics, and an 8 GB Apple M2), and two machines may count as tier 2: Nickolas's 16 GB RTX PRO 2000 Blackwell (confirmed) and Alex's RTX 5070 (**VRAM still unknown**). That backs D-008: most of the team builds on the `stub` backend and on cached results computed on a GPU machine.

Asked in the team chat on 2026-10-28. The questions were (1) GPU and VRAM, (2) OS, (3) other compute or credits you can use. Answers are as reported. Nobody has run `python -m vibe.modules doctor` on them yet, except where the "Checked" column says so.

## Answers

| Teammate | Answered | GPU / memory (as reported) | OS | Other compute or credits | Checked |
|---|---|---|---|---|---|
| Jonathan S. | 2026-10-28 | RTX 5060 Ti, 8 GB | Windows | FIU quantum computer, from 2027 | Not yet |
| Chris | 2026-10-28 | Integrated graphics | Windows 11 | $400 Snowflake credits, $30 Gemini API credits | Not yet |
| Pablo H. | 2026-10-28 | Apple M5 Pro (18-core CPU, 16-core GPU), 64 GB unified memory | macOS | 100 Claude API credits | Not yet |
| Paul A. | 2026-10-31 | RTX 3050, 4 GB | Windows 11 | None | Not yet |
| Alex W. | 2026-10-31 | **No answer** (RTX 5070 from earlier talks: 12 GB if desktop, 8 GB if laptop) | Not given | | Not yet |
| Luis P. | 2026-10-31 | RTX 4050 Laptop, 6 GB | Windows 11 | HiPerGator (UF) | Driver 555.97, from his F-10 handoff |
| Nickolas G. | 2026-10-31 | RTX PRO 2000 Blackwell, 16 GB | Windows 11 | MDC Makers Space | Not yet |
| Paul P. | 2026-11-01 | RTX 4060 Ti, 8 GB | Windows 11 | None given | `nvidia-smi` 2026-11-01: 8188 MiB, driver 610.60 (R-1) |
| Jean M. | 2026-11-01 | RTX 3050 Laptop, 4 GB, driver 592 | Windows | None | Driver as reported |
| Eduardo G. | 2026-11-05 | Apple M2, 8 GB unified memory | macOS 26.6.2 | | `sysctl` on the machine |

- **Jonathan's row is inferred.** The first answer had no name. It matches the GPU and the FIU quantum computer in R-1's table for Jonathan ([r1-planner-llm.md](r1-planner-llm.md#team-hardware-and-where-each-local-model-runs)).
- **The RTX PRO 2000's 16 GB** is as reported. R-1 links the card's spec page.

## What each machine can do

Tiers are those in [ARCHITECTURE "Compute"](../ARCHITECTURE.md#compute-everyone-can-work-whatever-their-machine): **0** stub backend and CPU-sized models only, **1** small or quantized models one at a time (~4–8 GB), **2** full models and benchmarks. The tiers come from the reported memory using the same rule as `doctor` (12 GB+ NVIDIA is tier 2, and Apple Silicon needs 16 GB+ for tier 1). They're for planning who runs what, not measurements.

| Teammate | Tier | Needs CUDA 12.8 (driver 570+) | Notes |
|---|---|---|---|
| Alex W. | 2 if 12 GB, 1 if 8 GB | Yes (Blackwell) | The reference machine in D-008. Confirm the VRAM |
| Nickolas G. | 2 | Yes (Blackwell) | Largest confirmed VRAM on the team |
| Pablo H. | 1 (Metal) | No CUDA | 64 GB fits big models, but CUDA-only libraries (TensorRT, custom kernels) won't run. Speeds aren't comparable to NVIDIA |
| Jonathan S. | 1 | Yes (Blackwell) | |
| Paul P. | 1 | No (Ada); driver 610 is fine for `cu128` | |
| Luis P. | 1 | No (Ada), but **driver 555.97 is too old for `cu128`**: update to 570+ | |
| Paul A. | 1, tight | No (Ampere) | 4 GB: one small model at a time |
| Jean M. | 1, tight | No (Ampere); driver 592 is fine | 4 GB |
| Eduardo G. | 0 | No CUDA | 8 GB unified memory is shared with the OS |
| Chris | 0 | No | |

## What follows from it

- **Alex needs to confirm the 5070's VRAM.** Run `uv run --directory backend python -m vibe.modules doctor` and post the output. It decides whether the 5070 fits D-008's "detector + tracker + identity together" for live mode (F-50).
- **The reference machine may need a second look.** If the 5070 turns out to be the 8 GB laptop version, Nickolas's 16 GB card is the stronger benchmark machine. Changing D-008 is a team decision, not one for this survey.
- **GPU work goes to:** Alex and Nickolas for benchmarks, F-50 and real end-to-end runs. Pablo can do Metal-only spot checks. Jonathan, Paul P. and Luis can develop one model at a time. Everyone else works on `stub` plus a copied `cache/<sha256>/`.
- **Three cards are Blackwell** (5070, 5060 Ti, RTX PRO 2000), so they need the `cu128` extra (PyTorch 2.7+). See the README's backend setup.
- **Other compute:** HiPerGator (Luis) and the MDC Makers Space (Nickolas) are options for tier 3 jobs. Under D-017 they count as third-party unless the team controls the machine, so footage needs the project opt-in. Pablo's Claude credits and Chris's Gemini credits cover hosted-LLM runs (R-1).

## Updating this

When someone runs `doctor` on their machine, replace their row's "as reported" values and fill in "Checked" with the date and the driver.
