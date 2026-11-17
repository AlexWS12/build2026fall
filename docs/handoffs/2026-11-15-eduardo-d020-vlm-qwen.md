---
author: eduardo
date: 2026-11-15
branch: eduardo/d020-vlm-qwen
features: [F-13]
base: v0.1.0 @ 1642a7d
head: deae1ab
status: ready for review
---

# D-020 proposed: Qwen3.5 as the VLM that judges descriptions for F-13

> **Team heads-up needed.** This picks the model for F-13's description judge, which the F-13 handoff says the team decides. It's marked **Proposed**, so it can still change. **Not posted yet:** eduardo needs to post it in the team chat and tag luis (F-13 owner).

## What changed
- **`docs/ARCHITECTURE.md`, decisions log:** new row D-020.
  - **Qwen3.5-4B**, 4-bit, for the `local` backend on 6 GB laptops (RTX 4050).
  - **Qwen3.5-9B** on the RTX 5070 through `remote`.
  - `stub` keeps the colour judge from luis's F-13 PR.
  - Answers per track as `yes` / `no` / `unsure`, on team machines only (D-017).
  - Alternates if it fails the test: InternVL3.5-4B, Gemma 4 E4B.
- No code changes. This follows luis's F-13 work (PR 33, `docs/handoffs/2026-11-10-luis-f13-targeting.md` on `luis/f13-targeting-by-description`), which left the VLM choice open.

## How to check it
Read the D-020 row in `docs/ARCHITECTURE.md`. Tests were run anyway (macOS, no GPU used): backend `uv run pytest` 1318 passed; frontend `npm test` 53 passed.

## Decisions made
- **Qwen3.5 over the other small open VLMs**, from published numbers only. MMMU-Pro, as reported by others: Qwen3.5-4B 65.4, Qwen3.5-9B 69.2, Gemma 4 E4B 52.6. All three are Apache 2.0.
- **Two sizes:** 4B so the 6 GB laptops can run it, 9B on the 5070 for accuracy.
- **Proposed, not Accepted.** These numbers are from other people on other hardware, and no public benchmark checks what F-13 needs most: never a confident wrong `no`.

Other candidates looked at (published numbers, none measured by us):

| Model | Sizes | License | Reported scores |
|---|---|---|---|
| Qwen3-VL | 2B, 4B, 8B | Apache 2.0 | 8B: MMMU 69.6, DocVQA 96.1; 4B: MMMU 59.5 or 67.4 (sources disagree) |
| InternVL3.5 | 1B, 2B, 4B, 8B | Apache 2.0 | 8B: MMMU 73.4 (paper); MMMU, not MMMU-Pro, so not comparable |
| MiniCPM-V 4.5 | ~8B | Apache 2.0 (card) | OpenCompass average 77.0 |
| Gemma 4 | E2B, E4B, 26B-A4B, 31B | Apache 2.0 | MMMU-Pro: E4B 52.6, E2B 44.2 |
| Moondream 3.1 | 9B total, 2B active | Moondream Model License (no general hosted service) | RefCOCO-M 87.1, CountBench 90.4 (vendor) |
| SmolVLM2 | 256M, 500M, 2.2B | Apache 2.0 | none found |

## Next steps
- Post the heads-up; get luis's and the team's OK on D-020.
- luis (F-13): build the Qwen3.5 judge behind the `Judge` interface in `vision/describe.py`, `local` first, then `remote` through F-08's model server.
- Measure before accepting: a script on a labeled, consented clip (F-09b, jean) that reports right / wrong / unsure and seconds per track, with the hardware stated. Accept D-020, or switch to an alternate, from that.
- Check VRAM of Qwen3.5-4B at 4-bit on an RTX 4050 for real; the ~6 GB figures I saw for 8–9B models are third-party estimates.

## Gotchas
- The D-020 row cites luis's F-13 handoff, which only exists on luis's branch until PR 33 merges.
- Sources disagreed on several scores (Qwen3-VL-4B MMMU, Qwen3.5-9B MMMU-Pro 69.2 vs 70.1). The ones in D-020 are from Artificial Analysis; check the official Qwen model cards before quoting them.
- Untracked `AGENTS.md` and `.agents/` (a Codex copy of CLAUDE.md and the `b-*` skills) are on eduardo's machine and deliberately left out of this branch.
