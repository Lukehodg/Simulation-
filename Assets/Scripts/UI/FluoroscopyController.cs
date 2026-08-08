using System;
using UnityEngine;
using CardioVR.Complications;

namespace CardioVR.UI
{
    /// The C-arm, the pedal and the contrast injector. Every second of screening
    /// and every ml of contrast is counted — both are scored at debrief, because
    /// minimising them is the habit the simulator exists to build.
    public class FluoroscopyController : MonoBehaviour
    {
        [SerializeField] ComplicationSystem complications;
        [SerializeField] Camera fluoroCamera;

        [Header("Dose")]
        [Tooltip("Reference dose rate in mGy per second of screening at 0 degrees.")]
        [SerializeField] float baseDoseRate = 1.8f;

        [Tooltip("Steep angulation lengthens the beam path and raises dose.")]
        [SerializeField] float angulationDoseGain = 0.9f;

        [Header("Contrast")]
        [SerializeField] float injectorFlowMlPerSecond = 4f;

        public float PrimaryAngleDegrees { get; private set; }
        public float SecondaryAngleDegrees { get; private set; }

        public bool IsScreening { get; private set; }
        public float FluoroSeconds { get; private set; }
        public float CumulativeDoseMilliGray { get; private set; }
        public float ContrastUsedMl { get; private set; }
        public bool LineIsPurged { get; set; } = true;

        public event Action<float> ContrastInjected;

        float activeInjectionMl;

        void Update()
        {
            if (!IsScreening) return;

            FluoroSeconds += Time.deltaTime;

            float steepness = (Mathf.Abs(PrimaryAngleDegrees) + Mathf.Abs(SecondaryAngleDegrees)) / 90f;
            CumulativeDoseMilliGray += baseDoseRate * (1f + steepness * angulationDoseGain) * Time.deltaTime;
        }

        public void SetPedal(bool down) => IsScreening = down;

        /// LAO/RAO is the primary angle, CRA/CAU the secondary.
        public void SetProjection(float primaryDegrees, float secondaryDegrees)
        {
            PrimaryAngleDegrees = Mathf.Clamp(primaryDegrees, -120f, 120f);
            SecondaryAngleDegrees = Mathf.Clamp(secondaryDegrees, -45f, 45f);

            if (fluoroCamera != null)
                fluoroCamera.transform.localRotation =
                    Quaternion.Euler(SecondaryAngleDegrees, PrimaryAngleDegrees, 0f);
        }

        public bool MatchesProjection(float primary, float secondary, float tolerance)
            => Mathf.Abs(Mathf.DeltaAngle(PrimaryAngleDegrees, primary)) <= tolerance
               && Mathf.Abs(Mathf.DeltaAngle(SecondaryAngleDegrees, secondary)) <= tolerance;

        public void BeginInjection() => activeInjectionMl = 0f;

        public void HoldInjection()
        {
            float delta = injectorFlowMlPerSecond * Time.deltaTime;
            activeInjectionMl += delta;
            ContrastUsedMl += delta;
        }

        public void EndInjection()
        {
            if (activeInjectionMl <= 0f) return;

            complications.NotifyContrastInjection(activeInjectionMl, LineIsPurged);
            ContrastInjected?.Invoke(activeInjectionMl);
            activeInjectionMl = 0f;
            LineIsPurged = true;
        }

        public float ActiveInjectionMl => activeInjectionMl;
    }
}
