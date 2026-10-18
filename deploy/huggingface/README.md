---
title: Step Wise Demo
emoji: 🏠
colorFrom: green
colorTo: gray
sdk: static
app_file: index.html
pinned: false
license: mit
short_description: Explore a real 3D room reconstruction and demo findings
---

# Step Wise · Interactive interface demo

Browse a fictional patient's home assessment, inspect eight source photos, and explore the actual room reconstructed from 39 photos using VGGT and Gaussian splats.

This is a **read-only interface demonstration**. The room geometry is a real offline reconstruction, served as the compressed `room.ksplat` from the source repository. The two rug/cable findings were flagged by live TypeSafe Jev runs on the source photos, with raw responses recorded in the source repository; the rug is model-localized, the cable is a reviewer-confirmed approximate pin, and metric scale is unvalidated. The patient profile is fictional. No live inference, uploads, saved reviews, or report release occurs, and no credentials are included.

The demo is a hackathon prototype, not a clinically validated tool.

Source: [Step Wise](https://github.com/jacopopper/amd-hack), reconstruction version `8be9805`. Code: MIT. Frontend dependencies, viewer libraries and bundled fonts retain their respective licenses. See `capture-data/vendor/LICENSE-*.txt` and `THIRD_PARTY_NOTICES.txt`.
