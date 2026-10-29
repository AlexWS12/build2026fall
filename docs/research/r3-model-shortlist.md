# R-3: Detection, Tracking & Identity Model Shortlist (F-10 / F-50 / F-52)

**Bottom line:** Build the pipeline on the permissive DETR family for detection, with RF-DETR (Apache 2.0, N–L sizes) as the primary choice and D-FINE/DEIMv2 as alternates. Add EgoBlur Gen2 (Apache 2.0) for faces and license plates, ByteTrack/OC-SORT through Roboflow `trackers` (Apache 2.0), SFace or AdaFace as the identity embedder, and SAM 2.1 (Apache 2.0) for masks. This combination avoids AGPL entirely and has no hard blocker on the RTX 5070 (sm_120) once PyTorch ≥ 2.7 with cu128 wheels is installed. YOLOE, every Ultralytics model, BoxMOT and YOLO-World carry AGPL or GPL terms. InsightFace's pretrained packs are non-commercial. Keep all of these off the default path unless the team explicitly agrees.

## TL;DR

- **Shortlist (all permissive, all sm_120-friendly):**
  - Detect: RF-DETR N/S/M/L (Apache 2.0, 30.5–33.9M params, 48.4–56.5 COCO AP at 2.3–6.8 ms on a T4 with TensorRT FP16), with D-FINE / DEIMv2 (Apache 2.0) as backups.
  - Faces and plates: EgoBlur Gen2 (Apache 2.0) for offline work. Meta's Project Aria page says both Gen 2 models are "approximately 400 MB each and have ~104 million parameters". For fast live face boxes, use YuNet (MIT). Wu et al. report it has "only 75 856 parameters" and runs at 1.6 ms per frame at 320×320 on an Intel i7-12700K.
  - Track: ByteTrack / OC-SORT / BoT-SORT through Roboflow `trackers` (Apache 2.0).
  - Identity: SFace (Apache 2.0) to start, AdaFace (MIT) to upgrade accuracy.
  - Masks: SAM 2.1 (Apache 2.0, 38.9M–224.4M params) or EdgeTAM (Apache 2.0).
- **Flag or avoid:**
  - YOLOE is AGPL-3.0 and built on Ultralytics. Ultralytics' own license page states that models trained with its code are AGPL too.
  - BoxMOT is AGPL-3.0. YOLO-World is GPL-3.0.
  - InsightFace code is MIT, but its buffalo_l/antelopev2 weights are "non-commercial research purposes only".
  - Re-ID weights (OSNet/FastReID/CLIP-ReID) are trained on Market-1501/MSMT17, which are research-only.
  - SAM 3/3.1 is a custom "SAM License": commercial use is allowed, but the weights are gated and carry use restrictions.
- **The F-50 spike should measure two pipelines at 720p and 1080p on the 5070:**
  - Live: RF-DETR-N/S (TensorRT FP16) + ByteTrack + identity embedding only on new or uncertain tracks.
  - Offline: RF-DETR-L + EgoBlur Gen2 + OC-SORT/BoT-SORT + AdaFace + SAM 2.1 mask propagation.
  - Budget: 33.3 ms per frame end-to-end for 30 fps live mode.

---

## Key Findings

1. **The licensing landscape splits cleanly.** The DETR-family real-time detectors are all Apache 2.0 for code and weights: RT-DETR/RT-DETRv2/RT-DETRv4, D-FINE, DEIM/DEIMv2, and RF-DETR N–L.\[1\] Almost every modern "YOLO" is AGPL (Ultralytics YOLOv5/v8/11/12/26, YOLOv10, YOLOE) or GPL (YOLO-World, YOLOv9).\[2\] YOLOX is the main permissive YOLO (Apache 2.0), but it dates from 2021 and is less accurate.\[2\]
2. **A wrapper can change the license of an otherwise permissive model.** RT-DETR is Apache 2.0 from `lyuwenyu/RT-DETR`, but the copy inside the `ultralytics` package is AGPL-3.0.\[1\]\[3\] Loading any model through Ultralytics pulls AGPL terms into the app.\[4\] The same applies to trackers: the reference ByteTrack/OC-SORT/BoT-SORT repos are MIT, but BoxMOT, the most popular "all trackers" wrapper, is AGPL-3.0.\[5\]\[6\]
3. **Weights licenses differ from code licenses in the face/identity space.** InsightFace (MIT code, non-commercial weights), SCRFD (MIT code, non-commercial official weights), torchreid/FastReID/CLIP-ReID (MIT/Apache code, weights trained on research-only datasets) and Grounding DINO (Apache code, weights licensing unanswered in GitHub issue #466) all have this split.\[7\]\[8\]\[9\]\[10\] Commercially clean identity options are SFace (Apache 2.0) and AdaFace (MIT code; weights distributed as MIT, but trained on WebFace4M, so dataset terms are a residual risk).\[11\]\[12\]
4. **RTX 5070 / sm_120 is a solved problem at the framework level and a risk only for custom CUDA ops.** The PyTorch 2.7 release post (April 23, 2025) announced "[Prototype] NVIDIA Blackwell Architecture Support" with pre-built CUDA 12.8 wheels and Triton 3.3, and PyTorch maintainer ptrblck confirmed on the PyTorch forums that "all of our stable and nightly releases starting with PyTorch 2.7.0 and built with CUDA 12.8 or newer already support the Blackwell family including sm_120." Every stable and nightly build from 2.7.0 on, built with CUDA ≥ 12.8, includes sm_120 kernels, and 2.8/2.9/2.10/2.11 continue that support. Pure-PyTorch models (RF-DETR, D-FINE, DEIM, RT-DETR, YOLOX, SAM 2.1, EdgeTAM, Cutie, AdaFace, SFace via OpenCV) need no recompilation. The known pain points are:
   - Grounding DINO's `MultiScaleDeformableAttention` `_C` extension, which must be compiled with `-gencode arch=compute_120,code=sm_120` (a documented RTX 5090 failure appears in Grounded-SAM-2 issue #114).\[13\]\[14\]\[15\]
   - SAM 2's optional `_C` op.
   - Any `flash_attn` dependency (Florence-2 remote code).
   - Packages that hard-pin old torch versions.
5. **SAM has moved past 2.1.** SAM 3 (released Nov 19, 2025, 848M params) adds text-prompted "concept" segmentation and tracking. SAM 3.1 (March 27, 2026) adds Object Multiplex; Meta's release notes claim a "~7x speedup at 128 objects on a single H100 GPU compared to the SAM 3 November 2025 release", though the revised paper's controlled comparison gives 5.2× at 128 objects. Both ship under Meta's custom SAM License rather than Apache 2.0, and inference cost scales with the number of tracked objects.\[16\]\[17\]\[18\] For this project, SAM 2.1 (Apache 2.0) remains the default mask propagator, and SAM 3.x is an optional offline "open-vocabulary mask" engine pending team review of the license.
6. **No published latencies exist for the RTX 5070 itself, so F-50 must generate them.** Vendor numbers are on T4 (RF-DETR, D-FINE, DEIM), V100 (YOLOX) and A100/H100 (SAM 2.1/SAM 3.1). One third-party data point exists on similar silicon: DEIMv2-N ran at 12.8 ms in TensorRT FP16 on an RTX 5070 Ti (end-to-end, a mini COCO subset).\[19\] Expect the 5070 to land between T4 and A100 numbers, but treat that as a hypothesis to test, not a fact.

---

## Details — Per-Model Analysis

### A. Object / Person Detection (F-10, F-50)

#### RF-DETR (Roboflow) — *recommended primary detector*
- **Task:** Real-time detection and instance segmentation, with a DINOv2 ViT backbone and NMS-free output. Published at ICLR 2026.\[20\]
- **License:**
  - The `rfdetr` package and the N/S/M/L detection weights are Apache 2.0.\[21\]
  - All segmentation sizes and the keypoint preview are also Apache 2.0.\[22\]
  - **XL/2XL detection (and Atto/Femto/Pico) are under PML 1.0 via `rfdetr[plus]`.**\[21\] Avoid these unless the team reviews PML.
- **Size and accuracy (T4, TensorRT 10.4, FP16, batch 1):**
  - N: 30.5M params, 48.4 AP, 2.3 ms @384
  - S: 32.1M, 53.0 AP, 3.5 ms @512
  - M: 33.7M, 54.7 AP, 4.4 ms @576
  - L: 33.9M, 56.5 AP, 6.8 ms @704
  - XL (PML): 126.4M, 58.6 AP
  - 2XL (PML): 126.9M, 60.1 AP, 17.2 ms\[22\]
  - Weights are roughly 120–135 MB in FP32 for N–L (params × 4 bytes).
- **CUDA 12.8:** Pure PyTorch, so it works on torch ≥ 2.7 cu128. ONNX/TensorRT export is supported.
- **Pros:**
  - Best accuracy/latency among the Apache options.\[21\]
  - Designed for fine-tuning (good if F-52 needs custom classes, e.g., screens or documents).\[21\]
  - Native `supervision` and `trackers` integration.
  - pip install; actively maintained.
- **Cons:**
  - Larger backbone than tiny YOLOs (about 30M params even at Nano).
  - Low input resolutions (384–704) can miss small faces and plates at 1080p.
  - Closed-set COCO classes only.
- **Trade-off:** It is the fastest path to accurate people and object boxes. Faces and plates still need a dedicated detector.

#### RT-DETR / RT-DETRv2 / RT-DETRv4 (Baidu; PKU/Tsinghua for v4)
- **Task:** Real-time NMS-free DETR detection.\[3\]
- **License:** Apache 2.0 for code and weights (`lyuwenyu/RT-DETR` for v1/v2, `RT-DETRs/RT-DETRv4` for v4). **The copy inside the `ultralytics` package is AGPL-3.0, so do not load it through Ultralytics.**\[1\]\[23\]
- **Accuracy:**
  - RT-DETR R18: 49.0 AP, about 4.6 ms on T4.\[24\]
  - RT-DETRv4: S/M/L/X at 49.8 / 53.7 / 55.4 / 57.0 AP, with L at 124 FPS.\[25\]
- **CUDA 12.8:** Pure PyTorch, so it is fine. The repo has TensorRT/ONNX deployment paths.
- **Pros:** Mature, widely reimplemented (HF Transformers, LibreYOLO with MIT code and Apache weights), and permissive.\[1\]
- **Cons:**
  - Version sprawl (v1/v2/v3/v4 across different repos).\[3\]
  - The official repo has many open issues (412 at the time of checking).\[26\]
  - Beaten by RF-DETR and D-FINE on accuracy per millisecond.
- **Trade-off:** A safe, well-understood baseline. Use it as the fallback if RF-DETR integration hits snags.

#### D-FINE (USTC) and DEIM / DEIMv2 (Intellindust)
- **Task:** Real-time DETRs. D-FINE refines box distributions. DEIM is a training recipe applied on top of D-FINE or RT-DETRv2. DEIMv2 adds DINOv3 features.\[27\]
- **License:** Apache 2.0 for code and weights, for all three.\[28\]\[29\]
- **D-FINE (T4, TRT 10.4, FP16):**
  - N: 4M params, 42.8 AP, 2.12 ms
  - S: 10M, 48.5 AP, 3.49 ms
  - M: 19M, 52.3 AP, 5.62 ms
  - L: 31M, 54.0 AP, 8.07 ms
  - X: 62M, 55.8 AP, 12.89 ms
  - With Objects365 pretraining: L reaches 57.3 AP and X reaches 59.3 AP.\[30\]
- **DEIM-D-FINE:** L 54.7 AP, X 56.5 AP at the same latency.\[25\]\[31\]
- **DEIMv2:**
  - S: 9.71M params, 50.9 AP; the first sub-10M model above 50 AP.
  - X: 50.3M params, 57.8 AP.
  - Pico: 1.5M params, 38.5 AP.\[32\]
- **CUDA 12.8:** Pure PyTorch. D-FINE is in HF Transformers. A third-party benchmark ran DEIMv2-N at 12.8 ms in TensorRT FP16 on an RTX 5070 Ti, which shows Blackwell works.\[19\]\[27\]
- **Pros:**
  - Very small models with high AP (good VRAM headroom when sharing the GPU with SAM 2 and embedders).
  - Objects365-pretrained variants are strong.
- **Cons:**
  - Research-grade repos with config-driven training.
  - The RF-DETR authors report that naive FP16 quantization of D-FINE collapsed accuracy to 0.5 AP, so FP16/TensorRT export must be validated.\[24\]
- **Trade-off:** These give the smallest footprint for a given AP. Choose them if VRAM is tight or RF-DETR Nano is too heavy for live mode.

#### YOLOX (Megvii) — *named candidate*
- **Task:** Anchor-free YOLO detector.\[33\]
- **License:** Apache 2.0 for code and weights.\[33\]
- **Size and accuracy:**
  - Nano: 0.91M params, 25.8 AP @416
  - Tiny: 5.06M, 32.8 AP
  - S: 9.0M, 40.5 AP, 9.8 ms on V100
  - L: 50.0 AP at 68.9 FPS on V100
  - X: 51.2 AP\[34\]\[35\]
- **CUDA 12.8:** The model itself is pure PyTorch. ONNX, TensorRT, ncnn and OpenVINO exports are provided.\[36\] The repo's setup/C++ bits are old, so pip installing from source may need dependency patching on modern torch (verify).
- **Pros:** Permissive, tiny variants, battle-tested in tracking (ByteTrack's reference detector is YOLOX). Many C++ deployments exist.
- **Cons:**
  - About 15 AP behind RF-DETR-L, and requires NMS.
  - Little active development (2021-era).
- **Trade-off:** The best choice only when an ultra-light, NMS-based CNN is needed (e.g., CPU fallback).

#### YOLOE (THU-MIG) — *named candidate, AGPL*
- **Task:** Open-vocabulary detection and segmentation with text, visual or prompt-free modes. Published at ICCV 2025.\[37\]
- **License:** **AGPL-3.0, and built on the Ultralytics codebase.** It is also shipped inside `ultralytics`. Ultralytics' license page says models trained with its code fall under AGPL too.\[2\]\[38\]
- **Pros:** Real-time open-vocabulary detection, a direct fit for "blur the coffee mug".
- **Cons:** It violates the card's "avoid AGPL" rule. Any network or service deployment would require releasing the full app source.\[4\]\[37\]
- **Verdict:** **Exclude by default.** If open vocabulary is needed, use OWLv2 / Florence-2 / Grounding DINO, or SAM 3 (license review).

#### YOLO-World (Tencent AILab-CVC)
- **License:** **GPL-3.0** (the README says "supported for commercial usage", but GPL copyleft applies on distribution). It is built on MMYOLO (also GPL-3.0).\[2\]\[39\]
- **Speed and accuracy:** YOLO-World-L reaches 35.0 AP on LVIS minival (fixed AP), at about 52 FPS on V100.\[40\]\[41\]
- **Verdict:** A copyleft risk similar to AGPL. Avoid unless the team accepts GPL.

#### Open-vocabulary alternatives (offline, planner-driven "blur X")
- **OWLv2 (Google):**
  - Apache 2.0 weights on the HF card; about 155M params, 620 MB in FP32.\[42\]\[43\]
  - Native in HF Transformers, so no custom ops.
  - Slower than real-time YOLOs; best run per keyframe and handed to a tracker or SAM 2.
- **Florence-2 (Microsoft):**
  - MIT, base 0.23B and large 0.77B params (large-ft is 1.54 GB in fp16).\[44\]\[45\]
  - Does phrase grounding, OD and captioning.\[46\]
  - Its remote code may import `flash_attn`, which has spotty sm_120 wheels; patch it out or use the native Transformers port.
- **Grounding DINO (IDEA-Research):**
  - Apache 2.0 code; **weights license unanswered (issue #466)**; Swin-T about 172M params.\[8\]
  - **It needs its `_C` deformable-attention extension compiled for sm_120.** Without it, the PyTorch fallback is much slower and FP32-only.\[14\]\[47\] The HF Transformers port avoids the compile.\[15\]
  - The maintenance looks stale (288 open issues).\[48\]
- **SAM 3 / SAM 3.1 (Meta):**
  - Text-prompted detect + segment + track in one model (848M params).\[16\]\[49\]\[50\]
  - **Custom SAM License** (commercial use allowed; prohibits military/ITAR uses and reverse engineering; gated download).\[49\]\[50\]
  - The official README installs PyTorch 2.10 with CUDA 12.8, so it is compatible.\[51\]
  - Heavy, and cost scales linearly with tracked objects (SAM 3). Mixpeek's facebook/sam3.1 model page claims SAM 3.1 "halves VRAM usage (8GB to 4GB FP16)", but this claim does not appear in Meta's release notes, so treat it as unverified.
- **Trade-off:** Open-vocabulary flexibility costs 1–2 orders of magnitude of speed compared with closed-set DETRs. Run these on keyframes in offline mode only, then propagate with a tracker or SAM 2.

### B. Face & License-Plate Detection (F-10, F-52)

#### EgoBlur (Meta Project Aria) — *named candidate, recommended for offline*
- **Task:** Face and license-plate detection for anonymization. Two separate Faster R-CNN (ResNeXt) models, trained on 23M images and 790M boxes.\[52\]
- **License:**
  - Apache 2.0 for code and models (GitHub, HF card and the Meta announcement).\[53\]\[54\]\[55\]
  - **Caveat:** The Project Aria download page asks you to submit an email and "abide by the model license agreement". Confirm that the downloaded weights' license file is Apache 2.0.\[56\]
- **Size and speed:**
  - Gen1 and Gen2 are the same size; Meta's Project Aria page states: "Both the Gen 2 face and license plate models are approximately 400 MB each and have ~104 million parameters."
  - Per the same page: "Based on a 2MP RGB image, both the EgoBlur Gen 2 face detection and license plate models run in approximately 21ms on a GPU and 1.5 seconds on a CPU" (the GPU model is not named).
- **CUDA 12.8:** TorchScript/PyTorch, Detectron2-trained. `egoblur` v2.0.1 is pip-installable (Python 3.10–3.12).\[57\] Detectron2 is not needed at inference for the TorchScript models (verify in F-50).
- **Pros:**
  - Purpose-built for exactly this privacy use case, including plates.\[58\]
  - Strong recall; evaluated across responsible-AI demographic labels.\[52\]
  - Permissive and maintained (Gen2, plus VRS support in 2.0.1).\[53\]
- **Cons:**
  - Heavy: running face and LP models together at 1080p roughly costs about 40 ms or more per frame, which is too slow for 30 fps live mode on every frame.
  - Boxes only (no masks), and no tracking or identity.\[56\]
- **Trade-off:** Use it for the highest recall on offline exports. In live mode, run it every N frames with a tracker filling the gaps, or substitute YuNet for faces.

#### YuNet + SFace (OpenCV Zoo) — *recommended live face path*
- **YuNet:**
  - MIT; Wu et al. (Machine Intelligence Research, Springer) report "only 75 856 parameters" and 1.6 ms per frame at 320×320 on an Intel i7-12700K.
  - WIDER FACE val AP: 0.834 / 0.824 / 0.708 (easy/medium/hard) in the original report; 0.884 / 0.866 / 0.750 in the OpenCV Zoo eval.
  - Best on faces of about 10–300 px.\[59\]
- **SFace:**
  - Apache 2.0, MobileFaceNet-based recognizer, LFW 99.40%.\[60\]
  - `cv2.FaceRecognizerSF` with `alignCrop` handles alignment directly from YuNet landmarks.\[12\]\[61\]
- **CUDA 12.8:** Runs on OpenCV DNN (CPU, or CUDA if OpenCV is built with CUDA) or ONNX Runtime GPU. No PyTorch dependency.
- **Pros:** Tiny, millisecond-level, fully commercial-safe detect + recognize chain, and trivial to integrate.\[60\]\[61\]
- **Cons:**
  - Weaker on hard (tiny, occluded or profile) faces than SCRFD or EgoBlur.\[62\]
  - SFace is far less discriminative than ArcFace/AdaFace on hard pairs.
- **Trade-off:** The best fit for live mode and the "is this me?" check on cooperative, frontal faces.

#### Other face detectors
- **SCRFD / RetinaFace (InsightFace):** Strong WIDER FACE Hard results, but the official pretrained weights are non-commercial (MIT code only).\[10\]\[60\]
- **MediaPipe BlazeFace:** Apache 2.0 and very fast, but tuned for short-range selfie faces. Weak for crowds or far faces.

### C. Multi-Object Tracking (F-10, F-50)

| Tracker | License | Appearance model? | Reported metrics | Notes |
|---|---|---|---|---|
| **ByteTrack** | MIT (reference); Apache 2.0 (Roboflow `trackers`) | No | MOT17 HOTA 63.1 (paper); 60.1 in `trackers` defaults; 1265 FPS (BoxMOT ablation) | Default choice.\[6\]\[63\] Keeps low-confidence boxes, which suits blur recall |
| **OC-SORT** | MIT / Apache 2.0 (`trackers`) | No\[6\] | MOT17 HOTA 61.9 and best on DanceTrack in `trackers` | Better for non-linear motion and occlusion recovery |
| **BoT-SORT** | MIT / Apache 2.0 (`trackers`) | Optional ReID + camera-motion compensation | HOTA 69.4, IDF1 81.8, 46 FPS (BoxMOT ablation, with ReID)\[6\]\[63\] | Best for handheld/moving cameras. ReID adds cost and weights-license risk |
| **SORT** | Apache 2.0 (`trackers`) | No | MOT17 HOTA 58.4 | Lightest baseline |
| **McByte** (in `trackers`) | Apache 2.0 | Mask-guided (SAM/Cutie) | MOT17 HOTA 64.1 (`trackers` best) | Highest accuracy, but pulls in SAM/Cutie dependencies |
| **Deep OC-SORT / StrongSORT / BoostTrack** | **AGPL-3.0 via BoxMOT** | Yes | Deep OC-SORT HOTA 67.8 at 12 FPS\[6\]\[63\] | Avoid the BoxMOT packaging; reference repos vary |
| **DeepSORT / Norfair** | Reference DeepSORT is older (GPL-3.0 in the original repo, per my recollection; verify); Norfair BSD-3 (verify) | DeepSORT yes | — | Superseded; not recommended |

- **Roboflow `trackers` (v2.6.0, Apache 2.0, Python 3.10+)** is the cleanest permissive wrapper. It has clean-room implementations of SORT, ByteTrack, OC-SORT, BoT-SORT, C-BIoU and McByte behind one API, and works natively with `supervision.Detections`.\[64\]\[65\]\[66\] **Recommended.**
- **BoxMOT** (about 8.3k stars, AGPL-3.0) is the most feature-complete wrapper, with C++ and TensorRT ReID.\[5\] **Excluded under the card rule.**\[67\]
- Note that the MOT17 numbers differ between sources (63.1 in the ByteTrack paper, 60.1 in `trackers` defaults, 67.7 on the BoxMOT ablation split) because detectors, splits and tuning differ.\[6\]\[63\] Compare trackers only within one benchmark.

### D. Mask Propagation / Segmentation (F-10, F-52)

#### SAM 2.1 (Meta) — *named candidate, recommended mask engine*
- **Task:** Promptable image and video segmentation with a streaming memory. You prompt it with detector boxes and it propagates masks across frames.\[68\]
- **License:** Apache 2.0 for code and checkpoints (the SA-V dataset is CC BY 4.0).\[16\]
- **Size and speed (A100; SA-V test J&F):**
  - Tiny: 38.9M params, 91.2 FPS (compiled), J&F 76.5
  - Small: 46M, 84.8 FPS, J&F 76.6
  - Base+: 80.8M, 64.1 FPS, J&F 78.2
  - Large: 224.4M, 39.5 FPS, J&F 79.5\[69\]
  - **The FPS figures depend on `torch.compile` / the `vos_optimized` predictor.** The repo's non-compiled table shows Tiny at 47.2 FPS and Large at 24.2 FPS (30.2 compiled). Treat the higher numbers as best case.\[16\]\[70\]
- **CUDA 12.8:** Pure PyTorch (≥ 2.5.1); the optional `_C` connected-components extension can be skipped. `torch.compile` on Blackwell relies on Triton ≥ 3.3, which ships with PyTorch 2.7+.\[71\]
- **Pros:** Pixel-accurate masks (better than box blur for "blur everyone except me"), handles occlusion via memory, permissive license, and a mature ecosystem.
- **Cons:**
  - Per-object cost; multi-person scenes multiply latency.
  - Runs at 1024² internally.\[68\]
  - Drift on long videos; needs periodic re-prompting from the detector.
- **Trade-off:** Use it in offline mode for quality. In live mode, start with box/ellipse blur and test SAM 2.1-Tiny or EdgeTAM as a stretch goal.

#### SAM 3 / 3.1
Covered under open vocabulary above. **Note the custom license.** Recommend it only for offline "segment all `<noun>`" requests after team review.

#### EdgeTAM (Meta)
- Apache 2.0 for code and checkpoints. The EdgeTAM paper (Zhou et al., CVPR 2025) reports it "runs 22× faster than SAM 2 and achieves 16 FPS on iPhone 15 Pro Max", where the smallest SAM 2 manages "only around 1 FPS", with 72.3 J&F on SA-V val.
- **Pros:** The most realistic candidate for live-mode masks.
- **Cons:** Lower mask quality than SAM 2.1-B+/L, and a smaller community.

#### Cutie / XMem / DEVA (Ho Kei Cheng)
- **Cutie:** MIT; pure PyTorch, so no sm_120 issues. It supersedes XMem (MIT, legacy).\[72\]\[73\]\[74\] A strong alternative mask propagator to SAM 2 for long videos.
- **DEVA:** Its license is unresolved. The HF card says GPL-3.0, and the repo `LICENSE.md` is non-standard. It also depends on Grounding DINO (custom CUDA op).\[75\]\[76\]\[77\] **Avoid** unless the license is clarified.

#### SAMURAI
- Apache 2.0 code; training-free and uses SAM 2.1 weights. Reports LaSOT AUC 74.2 (SAMURAI-L).\[78\]\[79\]
- Built for single-object visual tracking, so it is useful for robustly following "me" across occlusions in offline mode.\[80\]
- Low repo activity.

#### MobileSAM / EfficientSAM
Image-only lightweight SAMs (no video memory). Superseded by EdgeTAM for this use case.

### E. Identity (F-52: "everyone except me")

#### InsightFace / ArcFace — *named candidate, license flag*
- **Code:** MIT. **Pretrained models: "available for non-commercial research purposes only"**, including auto-downloaded packs. Commercial licensing is available through recognition-oss-pack@insightface.ai.\[81\]\[82\]
- **Packs:**
  - buffalo_l: SCRFD-10GF + ResNet50@WebFace600K, 326 MB.\[83\]
  - antelopev2: SCRFD-10GF + ResNet100@Glint360K, 407 MB.\[83\]
  - buffalo_s: about 159 MB.\[60\]
- InsightFace 2.0/2.1 (Sep/Oct 2026) adds raccoon packs and PersonAnalysis (body ReID), under the same non-commercial weight terms.\[82\]\[84\]
- **CUDA 12.8:** ONNX Runtime GPU, so no torch custom ops (needs an onnxruntime-gpu build compiled against CUDA 12.x; verify sm_120 provider support in F-50).
- **Pros:** Best-in-class accuracy, a one-line `FaceAnalysis()` API, and detection + alignment + embedding in one package.
- **Cons:** **The non-commercial weights are acceptable for a class demo but not for a product.** They are also large.\[85\]
- **Verdict:** Use only as an accuracy reference in experiments; do not ship.

#### AdaFace — *recommended accuracy upgrade*
- **License:** MIT code, and the weights are distributed as MIT. They are trained on WebFace4M/12M or MS1MV2/3, whose dataset terms are research-oriented. **Flag the dataset risk.**\[11\]\[86\]
- **Accuracy (IJB-C TAR@FAR=0.01%):**
  - IR-18 (WebFace4M): 94.99, ONNX about 92 MB
  - IR-50: 96.98, about 166 MB
  - IR-100: 97.39\[86\]\[87\]
- **CUDA 12.8:** Plain PyTorch IResNet; ONNX ports are available.\[87\]
- **Pros:** Quality-adaptive margin that is robust on low-quality, blurry video faces, which matters at 720p and for small faces.\[88\]
- **Cons:** No bundled detector (pair it with YuNet/EgoBlur + 5-point alignment), and the training-data provenance question.

#### SFace (OpenCV Zoo)
- Apache 2.0, LFW 99.40%, MobileFaceNet-sized. The simplest commercially clean baseline.\[12\]\[60\] See section B.

#### FaceNet (facenet-pytorch)
- MIT code, Inception-ResNet-v1 trained on VGGFace2/CASIA-WebFace, v2.6.0.\[89\]\[90\]
- **Dataset rights for VGGFace2 are separate from the MIT code license.**\[91\]
- **Risk:** Recent facenet-pytorch releases pin an older torch range. Install with `--no-deps` on torch ≥ 2.7 and test (verify).
- Older and less accurate than AdaFace.

#### DeepFace / Dlib
- These are wrappers around many backends (each backend's weights have their own license). They are convenient for prototyping but add an opaque license surface. Not recommended for the shortlist.

#### Person re-ID (body appearance, for when the face is not visible)
- **torchreid/OSNet:** MIT code. `osnet_x0_25` has 0.2M params and reaches Market-1501 rank-1 91.2 / mAP 75.0. **Weights are trained on Market-1501/MSMT17, which are research-only.**\[92\]\[93\]\[94\]\[95\] Unmaintained since 2021.
- **FastReID:** Apache 2.0 code, with the same dataset-weights restriction. Inactive since Jul 2024.\[9\]\[96\]
- **CLIP-ReID:** MIT code, with the same restriction.\[97\]
- **Trade-off:** Body re-ID helps keep "me" unblurred when I turn away from the camera. It is commercially clean only if retrained on licensed data or used with ImageNet-only backbones. **Use it for experiments and keep it out of the default pipeline.**

---

## Shortlist Table (proposed for docs/ARCHITECTURE.md)

| Model | Task | License (code / weights) | Weights size | CUDA 12.8 / sm_120 | Speed (reported, hardware) | Notes |
|---|---|---|---|---|---|---|
| **RF-DETR N / S / M / L** | detect | Apache 2.0 / Apache 2.0 (XL/2XL = PML 1.0) | 30.5–33.9M params (≈120–135 MB FP32) | ✅ pure PyTorch; ONNX/TRT | 2.3 / 3.5 / 4.4 / 6.8 ms (T4, TRT FP16) | **Primary**. 48.4–56.5 COCO AP\[21\]\[22\] |
| D-FINE N–X | detect | Apache 2.0 / Apache 2.0 | 4M–62M params | ✅; in HF Transformers | 2.12–12.89 ms (T4, TRT FP16) | 42.8–55.8 AP (59.3 with O365). Validate FP16 |
| DEIMv2 S / X | detect | Apache 2.0 / Apache 2.0 | 9.71M / 50.3M params | ✅ (DEIMv2-N 12.8 ms on RTX 5070 Ti, TRT FP16) | — | 50.9 / 57.8 AP. Smallest footprint |
| RT-DETR / v2 / v4 | detect | Apache 2.0 / Apache 2.0 (**AGPL if via Ultralytics**) | ~20–76M params | ✅ | RT-DETRv4-L 124 FPS (T4) | Fallback baseline\[1\]\[25\] |
| YOLOX Nano–X | detect | Apache 2.0 / Apache 2.0 | 0.91M–99M params (S = 9.0M) | ✅ model; old repo deps (verify) | S 9.8 ms; L 68.9 FPS (V100) | 25.8–51.2 AP. CPU/edge fallback |
| YOLOE | detect (open-vocab) + seg | **AGPL-3.0** / AGPL-3.0 | — | ✅ | real-time | **Excluded (AGPL, Ultralytics-based)**\[37\]\[98\] |
| YOLO-World | detect (open-vocab) | **GPL-3.0** / GPL-3.0 | ~100M params (L) | ✅ | ~52 FPS (V100) | Excluded unless GPL accepted |
| OWLv2 B/16 | detect (open-vocab) | Apache 2.0 / Apache 2.0 | ~155M params, 620 MB\[42\]\[43\] | ✅ HF Transformers | not real-time | Offline keyframes |
| Florence-2 B / L | detect/ground (open-vocab) | MIT / MIT | 0.23B / 0.77B params\[44\]\[99\] | ⚠️ patch out `flash_attn` | not real-time | Offline keyframes |
| Grounding DINO T | detect (open-vocab) | Apache 2.0 / **unclear (#466)** | ~172M params | ⚠️ compile `_C` for sm_120 or use HF port | not real-time | License question open\[8\]\[15\] |
| **EgoBlur Gen2 (face, LP)** | detect (faces, plates) | Apache 2.0 / Apache 2.0 (gated download) | ~400 MB, ~104M params each (Gen2, per Project Aria page) | ✅ PyTorch/TorchScript | ~21 ms per 2MP image (GPU, unspecified) | **Primary offline PII detector** |
| **YuNet** | detect (faces) | MIT / MIT | 75,856 params (<1 MB) | ✅ OpenCV/ORT | 1.6 ms @320×320 (i7-12700K CPU) | **Live face boxes**. WIDER hard 0.708–0.750 |
| SCRFD / RetinaFace (InsightFace) | detect (faces) | MIT / **non-commercial** | 0.5–10 GF variants | ✅ ORT | fast | Reference only\[10\]\[60\] |
| **ByteTrack** (Roboflow `trackers`) | track | Apache 2.0 (ref. MIT) / no weights | — | ✅ CPU | ~1265 FPS (BoxMOT ablation) | **Default tracker**\[6\]\[63\] |
| **OC-SORT** (`trackers`) | track | Apache 2.0 (ref. MIT) / — | — | ✅ CPU | ~1483 FPS (BoxMOT ablation) | Occlusion/non-linear motion\[6\]\[63\] |
| BoT-SORT (`trackers`) | track | Apache 2.0 (ref. MIT) / ReID weights vary | — | ✅ | 46 FPS with ReID (BoxMOT) | Moving camera (CMC)\[6\]\[63\] |
| BoxMOT (all trackers) | track | **AGPL-3.0** | — | ✅ | — | **Excluded**\[63\]\[67\] |
| **SAM 2.1 T / S / B+ / L** | segment + propagate | Apache 2.0 / Apache 2.0 | 38.9M / 46M / 80.8M / 224.4M params | ✅ (optional `_C` skippable) | 91.2 / 84.8 / 64.1 / 39.5 FPS (A100, compiled) | **Mask engine (offline)** |
| EdgeTAM | segment + propagate | Apache 2.0 / Apache 2.0 | small (SAM 2 variant) | ✅ | 22× SAM 2; 16 FPS on iPhone 15 Pro Max (CVPR 2025 paper) | **Live-mask candidate** |
| Cutie | segment propagate | MIT / MIT (VOS-dataset trained)\[72\] | ~35M params (unverified) | ✅ pure PyTorch | — | Alternative to SAM 2 |
| SAM 3 / 3.1 | open-vocab segment + track | **SAM License** / SAM License (gated) | 848M params | ✅ (README: torch 2.10 + cu128) | 3.1: Meta claims ~7× vs SAM 3 at 128 objects (H100); 5.2× in paper's controlled test | Offline only, after license review |
| SAMURAI | single-object track (SAM 2) | Apache 2.0 / SAM 2.1 weights | = SAM 2.1 size | ✅ | — | LaSOT AUC 74.2. "Follow me" offline\[78\]\[79\] |
| **SFace** | identity (face) | Apache 2.0 / Apache 2.0\[60\] | MobileFaceNet (~few MB) | ✅ OpenCV/ORT | ms-level | **Live identity baseline**. LFW 99.40 |
| **AdaFace IR-18 / IR-50 / IR-100** | identity (face) | MIT / MIT (WebFace-trained) | 92 MB / 166 MB / — (ONNX) | ✅ | — | **Accuracy upgrade**. IJB-C 94.99 / 96.98 / 97.39 |
| InsightFace buffalo_l / antelopev2 | detect + identity | MIT / **non-commercial** | 326 MB / 407 MB\[10\]\[83\] | ✅ ORT-GPU (verify) | — | Reference only |
| facenet-pytorch | identity (face) | MIT / MIT (VGGFace2 data rights) | Inception-ResNet-v1 (~90 MB, unverified) | ⚠️ old torch pin (verify) | — | Legacy |
| OSNet / FastReID / CLIP-ReID | identity (body re-ID) | MIT / Apache / MIT; **weights research-only (Market/MSMT)** | 0.2M (OSNet x0.25) – ~86M\[9\]\[92\]\[95\]\[97\] | ✅ | — | Experiments only |

---

## Recommendations

### Pipeline 1 — Live mode (F-50 primary target)
1. **Detect people and objects:** RF-DETR-N (or -S) through TensorRT FP16 at 384–512 input, letterboxed from 720p/1080p. If over budget, fall back to D-FINE-S or DEIMv2-S.
2. **Detect faces:** YuNet on each person crop or the full frame. Optionally run EgoBlur face/LP every N frames (e.g., N=5) to catch misses.
3. **Track:** ByteTrack (Roboflow `trackers`) on persons, faces and plates. Use OC-SORT if occlusions are frequent.
4. **Check identity:** Compute an SFace embedding **only when a new face track is born or confidence drops**, then cache "me / not-me" per track ID. Upgrade to AdaFace IR-18 if false-accept rates are poor.
5. **Render:** Box/ellipse blur on the GPU. Masks (EdgeTAM / SAM 2.1-Tiny) are a stretch goal.
- **Licenses:** All Apache/MIT. No AGPL and no non-commercial weights.

### Pipeline 2 — Offline high-quality (export path)
1. **Detect:** RF-DETR-L (or D-FINE-X with Objects365) at full resolution. EgoBlur Gen2 face + LP on every frame.
2. **Handle open-vocabulary requests** from the planner ("blur the laptop screen"): OWLv2 or Florence-2 on keyframes. Optionally use SAM 3.1 if the team approves the SAM License.
3. **Track:** OC-SORT or BoT-SORT (CMC on for handheld footage), with a second pass for track stitching.
4. **Check identity:** AdaFace IR-50/IR-100 with a multi-image enrollment template of "me". Optionally use SAMURAI to follow the user's body when the face is lost.
5. **Segment:** SAM 2.1 Base+/Large prompted by tracked boxes, re-prompted every K frames to limit drift. Cutie is an alternative.

### What F-50 should measure
- **Environment:**
  - Record the exact `torch` / `torchvision` / CUDA / driver / TensorRT versions.
  - Confirm `torch.cuda.get_arch_list()` contains `sm_120`.\[100\]
  - Pin PyTorch ≥ 2.7 with cu128 (2.8+ recommended, since PyTorch 2.7 labeled Blackwell support a prototype).\[71\]
- **Matrix:** {RF-DETR-N, RF-DETR-S, D-FINE-S, DEIMv2-S, YOLOX-S} × {PyTorch FP32, PyTorch FP16/AMP, TensorRT FP16} × {720p, 1080p}.
- **Per-stage timings:**
  - Decode
  - Pre-process
  - Detector
  - Face detector (YuNet vs EgoBlur)
  - Tracker
  - Embedder (per call and amortized)
  - Blur/encode
  - p50 / p95 / p99 end-to-end
- **Budget:** ≤ 33.3 ms p95 for 30 fps; note the 60 fps (16.7 ms) headroom.
- **VRAM:** Peak VRAM with all models co-resident. The RTX 5070 has 12 GB, so check SAM 2.1-L + detector + embedder fit.
- **Accuracy sanity:**
  - Face recall on a small hand-labeled 720p/1080p clip set (small and far faces are the failure mode at 384–704 detector inputs).
  - "Me" false-accept and false-reject rates.
- **Validate FP16/TensorRT accuracy for D-FINE specifically,** given the reported FP16 collapse.\[24\]

### Proposed decisions-log row (docs/ARCHITECTURE.md) — *to propose to the team first*

| Date | Decision | Options considered | Rationale | Status |
|---|---|---|---|---|
| 2026-10-xx | Detection: RF-DETR (N/S live, L offline). PII: EgoBlur Gen2 + YuNet. Tracking: Roboflow `trackers` (ByteTrack default, OC-SORT/BoT-SORT alt). Identity: SFace → AdaFace. Masks: SAM 2.1 (EdgeTAM for live). | RT-DETRv2/v4, D-FINE, DEIMv2, YOLOX, YOLOE, YOLO-World, OWLv2, Florence-2, Grounding DINO, SAM 3.1, BoxMOT, InsightFace, OSNet/FastReID | All Apache/MIT; no AGPL or non-commercial weights; pure PyTorch/ONNX, so sm_120-compatible on PyTorch ≥ 2.7 cu128; best published accuracy per latency | Proposed. Pending F-50 latency results |

---

## Caveats

- **Re-check all latency numbers on the 5070.** They come from different GPUs (T4, V100, A100, H100) and different runtimes, so they are not directly comparable, and no vendor publishes RTX 5070 figures.
- **Some license items need a final check:**
  - EgoBlur's gated download agreement text.\[56\]
  - Grounding DINO weights (issue #466).\[8\]
  - DEVA's `LICENSE.md`.\[77\]
  - The PML 1.0 terms for RF-DETR XL/2XL.\[21\]
  - Dataset-derived restrictions on AdaFace (WebFace), FaceNet (VGGFace2) and all re-ID weights (Market-1501/MSMT17 are explicitly research-only).\[91\]\[94\]\[95\]
  - Since this is a university project, non-commercial weights may be legally fine for coursework, but the card's intent ("avoid AGPL unless the team agrees") suggests the team wants a shippable license posture. Ask the team to state that intent explicitly in the decisions log.
- **Several figures are from secondary sources or marked unverified:**
  - SAM 3.1 VRAM halving (from Mixpeek's model page, not Meta's release notes).
  - Cutie params.
  - facenet-pytorch's torch pin.
  - YOLOX repo dependency issues.
  - DeepSORT/Norfair licenses.
  - ONNX Runtime GPU sm_120 provider support.
- **Several version and release items come from secondary sites:** the InsightFace 2.x packs, SAM 3.1 timing, RF-DETR's ICLR 2026 status and BoxMOT's latest version (sources disagree between v19 and v25).\[101\] Confirm them on the GitHub release pages before writing them into ARCHITECTURE.md.
- **Privacy and ethics:** The identity component performs face recognition on bystanders. Even though it is used only to exclude the user from blurring, document the following in the handoff:
  - Enrollment consent.
  - That embeddings are stored locally.
  - That non-enrolled faces are never matched against anything except the user template.

## Sources

1. [RT-DETR Without Ultralytics: v1, v2 and v4 Under Apache-2.0](https://www.libreyolo.com/articles/rt-detr-without-ultralytics)
2. [Every YOLO License, Explained: v1 to YOLO26 (2026 Guide)](https://www.libreyolo.com/articles/yolo-licenses-explained)
3. [Hivebook - The Knowledge Base for AI Agents](https://hivebook.wiki/wiki/rt-detr-real-time-detection-transformer-nms-free)
4. [Is YOLO Free for Commercial Use? Licenses Explained](https://www.rizwanai.com/licensing)
5. [boxmot - Top Open Source AI Projects](https://topopensource.ai/projects/mikel-brostrom-boxmot)
6. [ByteTrack vs DeepSORT vs OC-SORT vs BoT-SORT (2026 Guide)](https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/multi-object-tracking-deepsort-bytetrack-ocsort)
7. [GitHub - KaiyangZhou/deep-person-reid: Torchreid: Deep learning person re-identification in PyTorch. · GitHub](https://github.com/KaiyangZhou/deep-person-reid)
8. [Does the Apache-2.0 license cover the pretrained weights (groundingdino\_swint\_ogc.pth), including commercial inference, deployment and redistribution? · Issue #466 · IDEA-Research/GroundingDINO · GitHub](https://github.com/IDEA-Research/GroundingDINO/issues/466)
9. [GitHub - JDAI-CV/fast-reid: SOTA Re-identification Methods and Toolbox · GitHub](https://github.com/JDAI-CV/fast-reid)
10. [Face Analysis, APIs & Model Licensing](https://www.insightface.ai/)
11. [neilhawkins/adaface-ir50-coreml · Hugging Face](https://huggingface.co/neilhawkins/adaface-ir50-coreml)
12. [opencv/face\_recognition\_sface · Hugging Face](https://huggingface.co/opencv/face_recognition_sface)
13. [Regarding installing Grounded-SAM2 on 5090: ms\_deformable\_im2col\_cuda: no kernel image is available for execution on the device · Issue #114 · IDEA-Research/Grounded-SAM-2 · GitHub](https://github.com/IDEA-Research/Grounded-SAM-2/issues/114)
14. [Running Grounded-SAM-2, GroundingDINO, and PointNet++ on RTX 5090 — Setup Notes and Pitfalls](https://medium.com/@limyoonaxi/running-grounded-sam-2-groundingdino-and-pointnet-on-rtx-5090-setup-notes-and-pitfalls-478ffc79e457)
15. [groundingdino-py - conda-forge](https://anaconda.org/conda-forge/groundingdino-py)
16. [SAM 2 Rotoscoping: Sammie-Roto 2, Memory Module, SAM 3.1](https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/sam-2-for-video-memory-module-rotoscoping)
17. [SAM 3: Segment Anything with Concepts](https://arxiv.org/html/2511.16719v2)
18. [SAM 3 License - Usage Rules, Restrictions & Commercial Rights](https://sam3ai.com/license/)
19. [DEIMv2-N Benchmark Results](https://www.visionanalysis.org/model/deimv2-n)
20. [RF-DETR — Real-Time Object Detection & Segmentation by Roboflow](https://rfdetr.roboflow.com/)
21. [GitHub - roboflow/rf-detr: RF-DETR is a real-time object detection and segmentation model architecture developed by Roboflow, SOTA on COCO, designed for fine-tuning. \[ICLR 2026\] · GitHub](https://github.com/roboflow/rf-detr)
22. [RF-DETR: the first real-time detector past 60 AP, and its six sizes are one training run](https://ai.thesatyajit.com/articles/rf-detr)
23. [RT-DETR, RT-DETRv2 and RT-DETRv4 in LibreYOLO](https://www.libreyolo.com/docs/models/rt-detr)
24. [RF-DETR: Neural Architecture Search for Real-Time Detection Transformers](https://arxiv.org/pdf/2511.09554)
25. [RT-DETRv4: Painlessly Furthering Real-Time Object Detection with Vision Foundation Models](https://arxiv.org/pdf/2510.25257)
26. [GitHub - lyuwenyu/RT-DETR: \[CVPR 2024\] Official RT-DETR (RTDETR paddle pytorch), Real-Time DEtection TRansformer, DETRs Beat YOLOs on Real-time Object Detection. 🔥 🔥 🔥 · GitHub](https://github.com/lyuwenyu/RT-DETR)
27. [Best Ultralytics Alternatives in 2026](https://www.libreyolo.com/articles/best-ultralytics-alternatives)
28. [D-FINE: Overview, Resources & Licensing](https://playground.roboflow.com/models/ustc/d-fine)
29. [DEIM: Overview, Resources & Licensing](https://playground.roboflow.com/models/intellindust-ai-lab/deim)
30. [GitHub - Peterande/D-FINE: D-FINE: Redefine Regression Task of DETRs as Fine-grained Distribution Refinement \[ICLR 2025 Spotlight\] · GitHub](https://github.com/Peterande/D-FINE)
31. [GitHub - JanickLntz/DEIM: \[CVPR 2025\] DEIM: DETR with Improved Matching for Fast Convergence · GitHub](https://github.com/JanickLntz/DEIM)
32. [Real-Time Object Detection Meets DINOv3](https://arxiv.org/html/2509.20787v4)
33. [YOLOX: Overview, Resources & Licensing](https://playground.roboflow.com/models/megvii/yolox)
34. [YOLOX/docs/model\_zoo.md at main · Megvii-BaseDetection/YOLOX · GitHub](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/docs/model_zoo.md)
35. [\[2107.08430\] YOLOX: Exceeding YOLO Series in 2021 - ar5iv](https://ar5iv.labs.arxiv.org/html/2107.08430)
36. [GitHub - Megvii-BaseDetection/YOLOX: YOLOX is a high-performance anchor-free YOLO, exceeding yolov3\~v5 with MegEngine, ONNX, TensorRT, ncnn, and OpenVINO supported. Documentation: https://yolox.readthedocs.io/ · GitHub](https://github.com/megvii-basedetection/yolox)
37. [YOLOE: Overview, Resources & Licensing](https://playground.roboflow.com/models/thu-mig/yoloe)
38. [Ultralytics License](https://www.ultralytics.com/license)
39. [GitHub - AILab-CVC/YOLO-World: \[CVPR 2024\] Real-Time Open-Vocabulary Object Detection · GitHub](https://github.com/ailab-cvc/yolo-world)
40. [YOLO-World-L by AILab-CVC](https://mixpeek.com/model/AILab-CVC/YOLO-World-L)
41. [zsxkib/yolo-world](https://internal.replicate.com/zsxkib/yolo-world/readme)
42. [model.safetensors · google/owlv2-base-patch16-ensemble at 410d70ced26e95c344915c2f10f4ecf967f2cde4](https://huggingface.co/google/owlv2-base-patch16-ensemble/blob/410d70ced26e95c344915c2f10f4ecf967f2cde4/model.safetensors)
43. [owlv2-base-patch16-ensemble](https://www.promptlayer.com/models/owlv2-base-patch16-ensemble/)
44. [GitHub - anyantudre/Florence-2-Vision-Language-Model: Florence-2 is a novel vision foundation model with a unified, prompt-based representation for a variety of computer vision and vision-language tasks. · GitHub](https://github.com/anyantudre/Florence-2-Vision-Language-Model)
45. [microsoft/Florence-2-large-ft at main](https://huggingface.co/microsoft/Florence-2-large-ft/tree/main)
46. [Florence-2-large-ft: Text-to-Image model — overview, use cases, alternatives](https://www.aimodels.fyi/models/huggingFace/florence-2-large-ft-microsoft)
47. [Fix Grounded-SAM crashing on GPUs without the compiled GroundingDINO ops by RoberAlcaraz · Pull Request #3 · RoberAlcaraz/ReMatch · GitHub](https://github.com/RoberAlcaraz/ReMatch/pull/3)
48. [GitHub - IDEA-Research/GroundingDINO: \[ECCV 2024\] Official implementation of the paper "Grounding DINO: Marrying DINO with Grounded Pre-Training for Open-Set Object Detection" · GitHub](https://github.com/idea-research/groundingdino)
49. [AEmotionStudio/sam3 · Hugging Face](https://huggingface.co/AEmotionStudio/sam3)
50. [Meta SAM 3.1: What Changed, Benchmarks and Local Setup](https://vucense.com/ai-intelligence/open-source-ai/meta-sam-3-1-release-video-segmentation-local-ai/)
51. [SAM 3.1: Object Multiplex and Video Tracking](https://learnopencv.com/sam-3-whats-new/)
52. [EgoBlur](https://facebookresearch.github.io/projectaria_tools/docs/open_models/egoblur)
53. [GitHub - facebookresearch/EgoBlur: This repository contains a command-line interface(CLI) that can detect and blur out faces and license plates(PII) from images and videos. The CLI takes an image or video file as input, runs an anonymization algorithm on it, and writes the blurred output to a specified path. · GitHub](https://github.com/facebookresearch/EgoBlur)
54. [projectaria/EgoBlur · Hugging Face](https://huggingface.co/projectaria/EgoBlur)
55. [Used internally by Project Aria, Meta’s device for conducting egocentric research, EgoBlur is used to detect and then remove faces and license plates from RGB or grayscale images that have been captured in unconsented environments. 2/6](https://www.threads.com/@edwardrichardmiller/post/Cwn_pK4gCo4/used-internally-by-project-aria-metas-device-for-conducting-egocentric-research-)
56. [EgoBlur: a new AI model from Meta to preserve privacy in egocentric data](https://www.projectaria.com/tools/egoblur/)
57. [EgoBlur/pyproject.toml at main · facebookresearch/EgoBlur · GitHub](https://github.com/facebookresearch/EgoBlur/blob/main/pyproject.toml)
58. [(PDF) EgoBlur: Responsible Innovation in Aria](https://www.researchgate.net/publication/373437592_EgoBlur_Responsible_Innovation_in_Aria)
59. [pollen-robotics/face\_detection\_yunet\_2023mar · Hugging Face](https://huggingface.co/pollen-robotics/face_detection_yunet_2023mar)
60. [Face Recognition :: LocalAI](https://localai.io/docs/features/face-recognition/index.html)
61. [CV YuNet Face Detect - ComfyUI Cloud](https://comfy.icu/node/CV_YuNetFaceDetect)
62. [Top Free Face Detection APIs & Open-Source Models (2026)](https://www.edenai.co/post/top-free-face-detection-tools-apis-and-open-source-models)
63. [boxmot 16.0.6 on PyPI - Libraries.io - security & maintenance data for open source software](https://libraries.io/pypi/boxmot)
64. [Object Tracking in Python - Trackers](https://trackers.roboflow.com/develop/)
65. [Stop Wrestling with MOT Code! Use Roboflow Trackers Instead](https://www.blog.brightcoding.dev/2026/07/25/stop-wrestling-with-mot-code-use-roboflow-trackers-instead)
66. [A Comprehensive Guide to Roboflow Trackers: Running 6 Object Tracking Algorithms from SORT to the Latest McByte via a Single API｜アイドリ](https://note.com/ai_driven/n/ne71364a44702?hl=en)
67. [boxmot — BoxMOT: Pluggable python and c++ SOTA multi-object t…](https://olud.ai/project/mikel-brostrom-boxmot.html)
68. [robotflowlabs/sam2.1-hiera-small-int8 · Hugging Face](https://huggingface.co/robotflowlabs/sam2.1-hiera-small-int8)
69. [Building and Loading Models](https://deepwiki.com/facebookresearch/sam2/4-building-and-loading-models)
70. [Model Checkpoints](https://deepwiki.com/huggingface/segment-anything-2/2.2-model-checkpoints)
71. [PyTorch 2.7 Release - PyTorch](https://pytorch.org/blog/pytorch-2-7/)
72. [Cutie/LICENSE at main · hkchengrex/Cutie · GitHub](https://github.com/hkchengrex/Cutie/blob/main/LICENSE)
73. [Cutie/README.md at main · hkchengrex/Cutie · GitHub](https://github.com/hkchengrex/Cutie/blob/main/README.md)
74. [GitHub - hkchengrex/XMem: \[ECCV 2022\] XMem: Long-Term Video Object Segmentation with an Atkinson-Shiffrin Memory Model · GitHub](https://github.com/hkchengrex/XMem)
75. [Tracking Anything With DEVA - a Hugging Face Space by hkchengrex](https://huggingface.co/spaces/hkchengrex/Tracking-Anything-with-DEVA)
76. [Tracking Anything with Decoupled Video Segmentation Ho Kei Cheng1†](https://arxiv.org/pdf/2309.03903)
77. [GitHub - hkchengrex/Tracking-Anything-with-DEVA: \[ICCV 2023\] Tracking Anything with Decoupled Video Segmentation · GitHub](https://github.com/hkchengrex/Tracking-Anything-with-DEVA)
78. [github.com](https://github.com/yangchris11/samurai)
79. [SAMURAI: Adapting Segment Anything Model](https://arxiv.org/pdf/2411.11922v2)
80. <https://arxiv.org/html/2411.11922>
81. [intangles-insightface · PyPI](https://pypi.org/project/intangles-insightface/)
82. [GitHub - deepinsight/insightface: State-of-the-art 2D and 3D Face Analysis Project · GitHub](https://github.com/deepinsight/insightface)
83. [insightface/model\_zoo/README.md at master · deepinsight/insightface · GitHub](https://github.com/deepinsight/insightface/blob/master/model_zoo/README.md)
84. [InsightFace: tasks, hardware requirements and license — open AI model](https://chimitdorzhi.tech/en/ii-modeli/insightface/)
85. [Load insightface - ComfyUI Cloud](https://comfy.icu/node/LoadInsightface)
86. [GitHub - mk-minchul/AdaFace · GitHub](https://github.com/mk-minchul/adaface)
87. [GitHub - yakhyo/adaface-onnx: AdaFace: Quality Adaptive Margin for Face Recognition](https://github.com/yakhyo/adaface-onnx)
88. [AdaFace/README.md at master · mk-minchul/AdaFace · GitHub](https://github.com/mk-minchul/AdaFace/blob/master/README.md)
89. [facenet-pytorch · PyPI](https://pypi.org/project/facenet-pytorch/)
90. [facenet-pytorch 2.6.0 on PyPI - Libraries.io - security & maintenance data for open source software](https://libraries.io/pypi/facenet-pytorch)
91. [py-feat/facenet · Hugging Face](https://huggingface.co/py-feat/facenet)
92. [Model Zoo — torchreid 1.4.0 documentation](https://kaiyangzhou.github.io/deep-person-reid/MODEL_ZOO)
93. [GitHub - djidje/deep-person-reid-1: Torchreid: Deep learning person re-identification in PyTorch. · GitHub](https://github.com/djidje/deep-person-reid-1)
94. [ReID\_Market-1501](https://www.kaggle.com/datasets/rayiooo/reid_market-1501)
95. [MSMT17 RELEASE AGREEMENT](https://www.pkuvmc.com/agreement/RELEASE_AGREEMENT-MSMT17.pdf)
96. [JDAI-CV · GitHub](https://github.com/JDAI-CV)
97. [GitHub - Syliz517/CLIP-ReID: Official implementation for "CLIP-ReID: Exploiting Vision-Language Model for Image Re-identification without Concrete Text Labels" (AAAI 2023) · GitHub](https://github.com/Syliz517/CLIP-ReID)
98. [GitHub - THU-MIG/yoloe: YOLOE: Real-Time Seeing Anything \[ICCV 2025\] · GitHub](https://github.com/THU-MIG/yoloe)
99. [add\_model (#1) · microsoft/Florence-2-large at 33ee8e1](https://huggingface.co/microsoft/Florence-2-large/commit/33ee8e109da7446c30db5ac65fa417853dd9cc9e)
100. [Pytorch support for sm120 - Page 3 - deployment - PyTorch Forums](https://discuss.pytorch.org/t/pytorch-support-for-sm120/216099?page=3)
101. [boxmot · 1.2M downloads on PyPI](https://pepy.tech/projects/boxmot)
