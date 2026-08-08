using System;
using UnityEngine;

namespace CardioVR.Physiology
{
    public enum Rhythm
    {
        Sinus,
        SinusBradycardia,
        SinusTachycardia,
        VentricularEctopy,
        VentricularTachycardia,
        VentricularFibrillation,
        Asystole
    }

    /// A first-order haemodynamic model: vitals drift toward targets that
    /// interventions and complications move. Deliberately simple — it is tuned to
    /// teach recognition and response timing, not to be physiologically exact.
    public class PatientVitals : MonoBehaviour
    {
        [Header("Baseline")]
        [SerializeField] float baselineHeartRate = 72f;
        [SerializeField] float baselineSystolic = 128f;
        [SerializeField] float baselineDiastolic = 78f;

        [SerializeField] float driftPerSecond = 0.35f;

        public float HeartRate { get; private set; }
        public float Systolic { get; private set; }
        public float Diastolic { get; private set; }
        public float SpO2 { get; private set; } = 98f;
        public Rhythm Rhythm { get; private set; } = Rhythm.Sinus;

        public float MeanArterialPressure => Diastolic + (Systolic - Diastolic) / 3f;
        public bool IsUnstable => MeanArterialPressure < 60f
                                  || Rhythm == Rhythm.VentricularTachycardia
                                  || Rhythm == Rhythm.VentricularFibrillation
                                  || Rhythm == Rhythm.Asystole;

        public event Action<Rhythm> RhythmChanged;

        float targetHeartRate, targetSystolic, targetDiastolic;

        void Awake() => ResetToBaseline();

        public void ResetToBaseline()
        {
            HeartRate = targetHeartRate = baselineHeartRate;
            Systolic = targetSystolic = baselineSystolic;
            Diastolic = targetDiastolic = baselineDiastolic;
            SpO2 = 98f;
            SetRhythm(Rhythm.Sinus);
        }

        void Update()
        {
            float k = driftPerSecond * Time.deltaTime;
            HeartRate = Mathf.Lerp(HeartRate, targetHeartRate, k);
            Systolic = Mathf.Lerp(Systolic, targetSystolic, k);
            Diastolic = Mathf.Lerp(Diastolic, targetDiastolic, k);
        }

        /// Vagal response to arterial puncture or sheath manipulation:
        /// bradycardia with hypotension.
        public void ApplyVasovagalResponse(float severity)
        {
            severity = Mathf.Clamp01(severity);
            targetHeartRate = Mathf.Lerp(baselineHeartRate, 38f, severity);
            targetSystolic = Mathf.Lerp(baselineSystolic, 70f, severity);
            targetDiastolic = Mathf.Lerp(baselineDiastolic, 44f, severity);
            if (severity > 0.4f) SetRhythm(Rhythm.SinusBradycardia);
        }

        /// Contrast in a coronary transiently depresses conduction and drops pressure;
        /// a large bolus does it harder and for longer.
        public void ApplyContrastBolus(float volumeMl)
        {
            float load = Mathf.Clamp01(volumeMl / 12f);
            targetHeartRate = Mathf.Lerp(targetHeartRate, targetHeartRate - 14f, load);
            targetSystolic = Mathf.Lerp(targetSystolic, targetSystolic - 18f, load);
            if (load > 0.85f) SetRhythm(Rhythm.VentricularEctopy);
        }

        /// The catheter tip irritating myocardium or a coronary being damped.
        public void ApplyIschaemicInsult(float severity)
        {
            severity = Mathf.Clamp01(severity);
            targetSystolic = Mathf.Lerp(targetSystolic, 78f, severity);
            targetHeartRate = Mathf.Lerp(targetHeartRate, 118f, severity);

            if (severity > 0.8f) SetRhythm(Rhythm.VentricularFibrillation);
            else if (severity > 0.5f) SetRhythm(Rhythm.VentricularEctopy);
        }

        public void ApplyBloodLoss(float volumeMl)
        {
            float load = Mathf.Clamp01(volumeMl / 900f);
            targetSystolic = Mathf.Lerp(baselineSystolic, 64f, load);
            targetDiastolic = Mathf.Lerp(baselineDiastolic, 40f, load);
            targetHeartRate = Mathf.Lerp(baselineHeartRate, 132f, load);
            if (load > 0.5f) SetRhythm(Rhythm.SinusTachycardia);
        }

        public void SetRhythm(Rhythm rhythm)
        {
            if (Rhythm == rhythm) return;
            Rhythm = rhythm;
            RhythmChanged?.Invoke(rhythm);
        }

        /// Treatment hooks the trainee can trigger from the cath lab UI.
        public void GiveAtropine()
        {
            if (Rhythm != Rhythm.SinusBradycardia) return;
            targetHeartRate = baselineHeartRate;
            targetSystolic = baselineSystolic;
            targetDiastolic = baselineDiastolic;
            SetRhythm(Rhythm.Sinus);
        }

        public void GiveFluidBolus(float volumeMl)
        {
            float lift = Mathf.Clamp01(volumeMl / 500f) * 18f;
            targetSystolic = Mathf.Min(baselineSystolic, targetSystolic + lift);
            targetDiastolic = Mathf.Min(baselineDiastolic, targetDiastolic + lift * 0.6f);
        }

        public void Defibrillate()
        {
            if (Rhythm != Rhythm.VentricularFibrillation && Rhythm != Rhythm.VentricularTachycardia)
                return;

            SetRhythm(Rhythm.Sinus);
            targetHeartRate = baselineHeartRate;
            targetSystolic = baselineSystolic * 0.85f;
            targetDiastolic = baselineDiastolic * 0.85f;
        }
    }
}
