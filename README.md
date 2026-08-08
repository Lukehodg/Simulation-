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
motion: you grip a hub and **push, pull and twist**. Insertion is hand travel
along the shaft axis; roll is wrist rotation about it. Resistance comes back
through controller haptics.

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

Trauma is **cumulative and does not heal**. Rough handling early costs the trainee
later — that's the teaching point, not a bug.

## Layout

```
Assets/
  Scenarios/
    aorto-coronary-tree.json              anatomy dataset (editable outside Unity)
    diagnostic-coronary-angiography.json  procedure script
  Scripts/
    Vasculature/   VesselSegment, VesselNetwork, VesselNetworkLoader
    Catheter/      CatheterState, CatheterNavigator, CatheterProfile, CatheterInputDriver
    Interaction/   ICatheterInput, XRCatheterHandle, DesktopCatheterInput
    Physiology/    PatientVitals
    Complications/ ComplicationSystem
    Core/          ProcedureDefinition, ProcedureRunner
    Assessment/    PerformanceRecorder, DebriefReport
    UI/            FluoroscopyController
```

The anatomy and the procedure are **data, not code** — a clinician can add a
vessel, change a stenosis, or rewrite the step list in JSON without touching C#.

## Getting started

1. Open the project in **Unity 2022.3 LTS**.
2. `Edit → Project Settings → XR Plug-in Management` → enable **OpenXR** for your
   target, and add an interaction profile for your controllers.
3. Create a scene with:
   - a `CatheterNavigator` (assign the vessel network asset and a catheter profile),
   - a `CatheterInputDriver` alongside it, pointed at either `XRCatheterHandle`
     (VR) or `DesktopCatheterInput` (no headset — W/S advance, A/D torque),
   - `PatientVitals`, `ComplicationSystem`, `FluoroscopyController`,
     `PerformanceRecorder`, `ProcedureRunner` and `DebriefReport`, wired to each
     other via their inspector fields.
4. Press Play. Without a headset the desktop input drives the whole procedure.

To build the network from JSON at runtime instead of an authored asset, move the
dataset under a `Resources/` folder and call
`VesselNetworkLoader.FromResource("aorto-coronary-tree")`.

### Hooking up the VR handle

`XRCatheterHandle` is deliberately decoupled from any specific interaction
package. Point `handTransform` at your interactor, `shaftAxis` at the catheter
hub, and drive `IsGripped` from your grab events (XRI's `selectEntered` /
`selectExited`, or an equivalent). Everything else follows from hand motion.

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

- Fluoroscopic rendering of the vessel tree with contrast opacification
- Guidewire-then-catheter workflow (the wire leads, the catheter follows)
- Radial access as an alternative route, with subclavian tortuosity
- Instructor mode: inject a complication mid-run and grade the response
- Session export for review across a cohort
