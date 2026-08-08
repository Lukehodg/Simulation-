# Running CardioVR on a headset

## The framework choice

**Unity 2022.3 LTS · OpenXR · XR Interaction Toolkit 2.5 · Meta Quest 3 standalone.**

| Decision | Why |
|---|---|
| **OpenXR**, not a vendor SDK | One build runs on Quest, Pico, Vive, Varjo, Index and Windows MR. A training department buying headsets in three years should not need the app rewritten. |
| **XR Interaction Toolkit**, not hand-rolled input | The interactor/interactable model, grab handling and haptic plumbing are the parts you would otherwise rebuild badly. `CoaxialShaftInteractable` extends it rather than replacing it. |
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
| Choose a device | **Where you take hold decides what you move.** Between the sheath and the hub is catheter; beyond the hub is bare guidewire. There is no mode to select. |
| Advance / withdraw | Push or pull along the shaft axis. One metre of hand travel is one metre of device. |
| Torque | Twist your wrist about the shaft axis. |
| Re-grip | The catheter runs out of travel at the sheath and the wire at the hub — release, move back, grip again. This is the real constraint, not a simulation limit. |
| Stabilise | A second hand on the same device makes its tip track torque more faithfully and roughly halves buckling trauma. |
| Screen | Press the floor pedal, or bind a USB foot pedal to the pedal input action. Screening never latches. |
| Inject | Pick up the syringe and squeeze the trigger. Flow follows squeeze pressure. |
| Change projection | Press a stored projection on the tableside pad. |

## The wire leads, the catheter follows

The order a trainee is supposed to learn — wire up first, track the catheter over
it, pull the wire back before engaging — is not enforced by a checklist. It falls
out of the geometry, so getting it wrong feels wrong rather than being announced:

* **While the wire leads, the catheter is railed to the wire's path.** It cannot
  take a branch the wire did not take. So a catheter with the wire still through
  its tip simply cannot engage a coronary — the same reason it cannot in life,
  where the wire props it off the wall.
* **Past the wire tip the catheter is bare.** It steers freely again, but wall
  contact costs progressively more the further it has run unsupported. Unsupported
  catheter against a vessel wall is how dissections start.
* **The wire is 50 cm longer than the catheter, and no more.** It can only lead by
  about 40 cm before its back end disappears into the hub and there is nothing left
  to hold — and withdrawing the catheter eats exactly the same margin, which is why
  exchanging a catheter needs a longer wire than the one you came in on.
* **The J-tip cannot select a coronary at all.** It is blunt and oversized; its
  only job is to give the catheter a road to follow.

The simulator does not refuse any of this. Push a bare catheter to the root and it
will go — and the vessel will remember.

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
  GuidewireMesh ───── likewise, so both devices appear on fluoroscopy
  FemoralSheath ───── entry point; the shaft protrudes along its forward axis
Simulation ────────── two navigators + CoaxialSystem, vitals, complications,
                      fluoroscopy, scoring
C-Arm ─────────────── gantry, isocentre, orthographic fluoro camera → RenderTexture
FluoroMonitor ─────── displays the density buffer through the FluoroDisplay shader
PatientMonitor ────── synthesised rhythm strip
CoaxialShaft ──────── catheter collider to the hub, wire collider beyond it
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

Verified here: every C# source parses cleanly, and the core simulation model —
navigation, resistance, complications, scoring — was driven end to end through the
browser preview in `preview/`. Note that the preview is still the **single-catheter**
model; the coaxial guidewire workflow exists only in the Unity build and has not
been exercised anywhere.

**Not verified:** nothing in this repository has been compiled by Unity or run on
a headset, because neither was available in the environment where it was written.
Expect to fix package-version drift on first import — the most likely spots are
the XRI 2.x names (`ActionBasedController`, `XRBaseControllerInteractor
.xrController`, `interactorsSelecting`), all of which move in XRI 3.x. The
simulation layer under `Assets/Scripts/{Vasculature,Catheter,Physiology,
Complications,Core,Assessment}` has no XR dependency and should compile untouched.
