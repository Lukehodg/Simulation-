# Running CardioVR on a headset

## The framework choice

**Unity 2022.3 LTS · OpenXR · XR Interaction Toolkit 2.5 · Meta Quest 3 standalone.**

| Decision | Why |
|---|---|
| **OpenXR**, not a vendor SDK | One build runs on Quest, Pico, Vive, Varjo, Index and Windows MR. A training department buying headsets in three years should not need the app rewritten. |
| **XR Interaction Toolkit**, not hand-rolled input | The interactor/interactable model, grab handling and haptic plumbing are the parts you would otherwise rebuild badly. `CatheterShaftInteractable` extends it rather than replacing it. |
| **Quest 3 standalone**, not tethered PCVR | A skills lab can run ten headsets without ten workstations. Passthrough also allows a real table and a real sheath prop under the virtual anatomy later. |
| **Unity, not Unreal** | The simulation is a physiology and state-machine problem, not a rendering one. C# iteration and the medical-XR ecosystem win here; Unreal's advantage would be photorealistic anatomy, which is not the bottleneck. |

The one honest caveat is haptics. Controller rumble tells you *that* resistance
changed, not how much — real catheter feel needs a force-feedback interface.
`IHapticDriver` exists precisely so such a device can be dropped in without the
simulation knowing.

## First-time setup

1. Open the project in **Unity 2022.3 LTS**. Let it resolve the packages in
   `Packages/manifest.json`.

2. **Import the XRI starter assets.** `Window → Package Manager → XR Interaction
   Toolkit → Samples → Starter Assets → Import`. This provides the default input
   action asset that binds grip, trigger and controller poses.

3. **Enable OpenXR.** `Edit → Project Settings → XR Plug-in Management`:
   - Tick **OpenXR** under both *PC* and *Android*.
   - Under `OpenXR`, add the interaction profiles for your controllers
     (*Oculus Touch Controller Profile* for Quest).
   - For Quest, enable the **Meta Quest** feature group under the Android tab.

4. **Build the scene.** `CardioVR → Build Cath Lab Scene` from the menu bar.
   This generates `Assets/Scenes/CathLab.unity` along with the vessel network,
   procedure and material assets under `Assets/Generated/`. Rerun it any time the
   anatomy JSON changes.

5. On the two controller objects under `XR Origin → Camera Offset`, assign the
   imported **XRI Default Input Actions** to the `ActionBasedController`
   components, and assign the position/rotation actions on the camera's
   `TrackedPoseDriver`.

6. **Android build settings** for Quest: IL2CPP, ARM64, Vulkan, Linear colour
   space, and multiview stereo rendering.

## How the procedure is performed in VR

| Action | How |
|---|---|
| Advance / withdraw | Grip the catheter shaft and push or pull along its axis. One metre of hand travel is one metre of catheter. |
| Torque | Twist your wrist about the shaft axis. |
| Re-grip | When your hand reaches the sheath you are out of travel — release, move back along the shaft, and grip again. This is the real constraint, not a simulation limit. |
| Stabilise | A second hand on the shaft makes the tip track torque more faithfully and roughly halves buckling trauma. |
| Screen | Press the floor pedal, or bind a USB foot pedal to the pedal input action. Screening never latches. |
| Inject | Pick up the syringe and squeeze the trigger. Flow follows squeeze pressure. |
| Change projection | Press a stored projection on the tableside pad. |

## Scene structure

`CathLabBuilder` generates rather than commits the scene, because a Unity scene
file is a wall of GUIDs that nobody can review. The room's layout lives in code
you can read in a diff.

```
XR Origin ─────────── camera + two controllers, floor tracking origin
XR Session ────────── frame rate, render scale, tableside placement
Anatomy ───────────── 0.01 scale (dataset is in centimetres)
  Vessels ─────────── one radiopaque mesh per segment, Radiography layer
  CatheterMesh ────── rebuilt each frame along the traversed path
  FemoralSheath ───── entry point; the shaft protrudes along its forward axis
Simulation ────────── navigator, vitals, complications, fluoroscopy, scoring
C-Arm ─────────────── gantry, isocentre, orthographic fluoro camera → RenderTexture
FluoroMonitor ─────── displays the density buffer through the FluoroDisplay shader
PatientMonitor ────── synthesised rhythm strip
CatheterShaft ─────── the grabbable
FluoroPedal, ContrastSyringe, ProjectionPad
```

## How the fluoroscopic image is produced

It is a real projection, not a diagram:

1. Vessel and catheter meshes render on the `Radiography` layer through
   `CardioVR/Radiopaque`, which blends **additively** — overlapping structures sum
   the way absorption does along a beam path, and grazing angles read denser, so a
   contrast-filled tube is dark at its edges and lighter down the middle.
2. The C-arm camera is **orthographic**, orbiting the isocentre at the angles the
   gantry has slewed to. Angulation genuinely separates overlapping vessels.
3. `CardioVR/FluoroDisplay` inverts that density buffer into dark-on-light film,
   adds intensifier vignetting, and adds grain **inversely proportional to dose
   rate** — screen sparingly and the image is noisier. The trade-off is felt
   rather than explained.
4. When the pedal is released the camera stops rendering, so the last frame stays
   on the monitor: last image hold.

## What has and has not been verified

Verified here: every C# source parses cleanly, and the simulation model itself —
navigation, resistance, complications, scoring — was driven end to end through
the browser preview in `preview/`, which is a direct port of the same rules.

**Not verified:** nothing in this repository has been compiled by Unity or run on
a headset, because neither was available in the environment where it was written.
Expect to fix package-version drift on first import — the most likely spots are
the XRI 2.x names (`ActionBasedController`, `XRBaseControllerInteractor
.xrController`, `interactorsSelecting`), all of which move in XRI 3.x. The
simulation layer under `Assets/Scripts/{Vasculature,Catheter,Physiology,
Complications,Core,Assessment}` has no XR dependency and should compile untouched.
