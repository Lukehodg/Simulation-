# CardioVR — cardiology procedural trainer

A VR simulator for **interventional cardiology procedural skills**. The first
scenario is a diagnostic coronary angiogram from right femoral access: track a
catheter up the aorta, engage each coronary ostium by feel and torque, image both
systems, and close — while the simulator counts every second of screening, every
millilitre of contrast, and every millimetre of vessel wall you traumatise.

> **Training use only.** The physiology and anatomy here are schematic models
> tuned to teach recognition, sequencing and handling. They are not derived from
> patient imaging, have not been clinically validated, and must not be used to
> guide the care of any real patient.

## Why VR

The hard part of catheter work isn't knowing the steps — it's the hands. Advancing
against resistance, torquing a shaft to rotate a tip you can only see in
projection, and feeling when to stop. So the interaction is built around real
motion: you grip the shaft and **push, pull and twist**. Insertion is hand travel
along the shaft axis; roll is wrist rotation about it.

The detail that makes it VR rather than a game on a headset: your grip is a fixed
point *on the shaft*, so pushing carries your hand toward the sheath, and when it
arrives you have run out of travel and must **release and re-grip further back** —
exactly as in the lab. Put a second hand on the shaft to steady it and the tip
tracks torque more faithfully and buckles less.

There are two devices on that shaft, coaxially: catheter from the sheath to the
hub, bare guidewire beyond it. So **where you take hold decides what you move** —
no mode, no toggle, just the place your hand lands.

Stack: **Unity 2022.3 LTS · OpenXR · XR Interaction Toolkit · Quest 3 standalone**.
See [docs/VR_SETUP.md](docs/VR_SETUP.md) for the rationale and the build steps.

## What the simulation models

| System | Behaviour |
|---|---|
| **Vascular tree** | Graph of centreline segments (femoral → iliac → aorta → arch → root → coronaries) with per-segment radius, tortuosity and stenosis |
| **Navigation** | Tip advances along centrelines; branches are entered only when shaft **roll aligns with the ostium** within tolerance — otherwise the catheter buckles |
| **Resistance** | Friction scaling with inserted length and cumulative tortuosity, plus a spike on buckling or lumen mismatch |
| **Haemodynamics** | HR, BP, SpO₂ and rhythm drifting toward targets moved by vagal response, contrast load, ischaemia and blood loss |
| **Complications** | Per-vessel trauma accumulates and converts to dissection, then perforation; ostial dwell causes damping and ischaemia; unpurged lines cause air embolism |
| **Dose** | Screening time and cumulative mGy, with steep angulation costing more |
| **Assessment** | Every step, complication and overrun recorded, scored out of 100 with a graded debrief |
| **Wire-led technique** | The catheter is railed to the guidewire's path while the wire leads, so it cannot engage an ostium until the wire is pulled back — and running on past the wire tip is possible, but the vessel remembers |
| **Fluoroscopy** | A real orthographic projection through additively-blended radiopaque meshes, inverted to film; grain scales inversely with dose rate, and releasing the pedal leaves last image hold |

Trauma is **cumulative and does not heal**. Rough handling early costs the trainee
later — that's the teaching point, not a bug.

## Browser preview

`preview/index.html` is a self-contained, playable preview of the whole procedure —
the same anatomy dataset and the same navigation rules as the Unity build, rendered
on canvas. Open the file in any browser. It exists so the interaction model can be
reviewed by clinicians without a headset or a Unity licence.

`W`/`S` advance and withdraw · `A`/`D` torque · `Space` screen · `C` inject · `F` hold

The projection maths is real: the C-arm angles rotate the vessel tree, so LAO 40 /
CRA 20 genuinely opens the left main bifurcation the way it does in the lab.

## Layout

```
Assets/
  Scenarios/
    aorto-coronary-tree.json              anatomy dataset (editable outside Unity)
    diagnostic-coronary-angiography.json  procedure script
  Shaders/         Radiopaque, FluoroDisplay
  Scripts/
    Vasculature/   VesselSegment, VesselNetwork, VesselNetworkLoader, VesselMeshBuilder
    Catheter/      CatheterState, CatheterNavigator, CatheterProfile,
                   CoaxialSystem, CatheterRenderer
    Interaction/   ICoaxialInput, CoaxialShaftInteractable, HapticDriver,
                   ContrastSyringe, FluoroPedal, ProjectionPresetButton,
                   DesktopCoaxialInput
    Physiology/    PatientVitals
    Complications/ ComplicationSystem
    Core/          ProcedureDefinition, ProcedureLoader, ProcedureRunner
    Assessment/    PerformanceRecorder, DebriefReport
    UI/            FluoroscopyController, CArmRig, PatientMonitorRenderer
    XR/            XRSessionBootstrap
    Editor/        CathLabBuilder
```

The anatomy and the procedure are **data, not code** — a clinician can add a
vessel, change a stenosis, or rewrite the step list in JSON without touching C#.

## Getting started

1. Open the project in **Unity 2022.3 LTS** and let packages resolve.
2. Import the XRI **Starter Assets** sample and enable **OpenXR** — see
   [docs/VR_SETUP.md](docs/VR_SETUP.md) for the exact settings.
3. Run **`CardioVR → Build Cath Lab Scene`** from the menu bar.

That generates `Assets/Scenes/CathLab.unity` with the room, the anatomy, the
C-arm, the monitors and every component reference wired up. The scene is
generated rather than committed because a Unity scene file is a wall of GUIDs
nobody can review — this way the layout is code you can read in a diff, and an
anatomy change is one menu click away from being in the scene.

No headset? Swap the `CoaxialSystem`'s input source for `DesktopCoaxialInput`
(W/S advance, A/D torque, Tab to switch between wire and catheter), or just open
`preview/index.html`.

## Extending it

- **New anatomy** — add segments to the JSON. `ostiumRollDegrees` is how much
  torque from neutral engages that branch; `ostiumRollToleranceDegrees` is how
  forgiving it is. Tight tolerance on a small vessel makes it genuinely hard.
- **New procedures** — write another procedure JSON. The step goals cover access,
  navigation, ostial engagement, contrast, C-arm angulation, withdrawal,
  haemostasis and treating instability.
- **New catheters** — a `CatheterProfile` asset. French size drives lumen fit,
  `torqueResponse` drives how faithfully the tip follows the shaft, and
  `intendedOstiaIds` marks what the shape is designed for.

## Roadmap

- Contrast opacification in the 3D fluoroscopy path (the preview already does it)
- Blocking contrast injection while the wire is still through the catheter tip
- Porting the guidewire workflow into the browser preview, which is still single-catheter
- Radial access as an alternative route, with subclavian tortuosity
- Instructor mode: inject a complication mid-run and grade the response
- Session export for review across a cohort
