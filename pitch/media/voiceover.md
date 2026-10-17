# Voice-over for `step-wise-video.mp4`

The video is silent, 2:36, 1920×1080. Each block starts at the timecode shown and must fit inside it (about 2.5 words per second). Numbers are spelled out so the voice reads them correctly.

Suggested workflow in ElevenLabs: generate one clip per block, then place each one at its timecode in the editor (CapCut, DaVinci, iMovie). If a block comes out longer, change the seconds of that piece in `TIMELINE` in `scripts/build_video.py` and rebuild; the script prints the new timecodes:

```bash
uv run --with playwright python scripts/build_video.py
```

Tone: calm, warm, unhurried. A short pause where you see `…`.

---

## Intro · Maria

**0:00 · cover (11 s)**
> On Friday, Maria goes home. She's eighty-four. She broke her hip falling in her hallway at night. … And no one has ever seen the home she's going back to.

**0:11 · what Step Wise is (7 s)**
> Step Wise checks her home before she leaves: from phone photos to a plan, approved by a nurse.

## 01 · The problem

**0:19 · at work vs at home (11 s)**
> At work, safety is the law. At home, there are no rules. Italy counts eleven hundred and eighty-nine deaths a year at work… and about six thousand at home.

**0:30 · falls at home (9 s)**
> More than half of falls happen at home, and only one in ten older people gets advice. Yet an expert home visit cuts falls by thirty-eight percent.

**0:40 · the bottleneck (11 s)**
> That expert is the occupational therapist. Italy has three thousand: one for every five thousand older people. The CDC's fallback: review the home with the family. … Step Wise makes it possible.

## 02 · How it works

**0:51 · capture (10 s)**
> Jacopo, her grandson, photographs the home with his phone, room by room. The nurse adds Maria's profile: a walker, a new hip, up at night.

**1:01 · detection (11 s)**
> TypeSafe Jev asks visual questions: a rug? A cable on the floor? A grab bar? Each hazard comes with its evidence. What a photo can't show is left for a visit.

**1:12 · 3D reconstruction (13 s)**
> VGGT looks at all thirty-nine photos at once, works out where each was taken and how far away every pixel is, and the room appears in 3D. … Real output, from a real home.

**1:25 · reasoning (9 s)**
> Gemma 4 reads the evidence and Maria's profile, and explains why each hazard matters for her: her walker catches on that rug.

## 03 · The real app

**1:34 · title card (2 s)**
> Here's the real app.

**1:37 · 3D room (4 s)**
> The nurse opens Maria's home in 3D.

**1:42 · review (8 s)**
> Each hazard has its photo and its reason. She confirms, edits, excludes, or leaves it for a visit.

**1:51 · report (4 s)**
> Only what she confirms goes into the plan.

**1:59 · release (3 s)**
> Nothing reaches the family without her sign-off.

**2:02 · family link (4 s)**
> One private link, and her daughter has the plan.

## 04 · Why AMD

**2:06 · AMD (11 s)**
> Home photos and clinical data can't leave the hospital. One AMD Instinct MI300X holds both the 3D reconstruction and Gemma, on site. … And Gemma already runs on AMD's endpoint today.

**2:17 · who it is for (7 s)**
> Built around the discharge: nurses use it, families benefit, hospitals gain the review the CDC recommends.

## Close

**2:25 · close (7 s)**
> On Friday, Maria goes home. … This time, someone looked at the hallway.

**2:32 · end card (4 s)**
> *(silence, or just: "Step Wise.")*
