using System;
using UnityEngine;
using CardioVR.Assessment;
using CardioVR.Catheter;
using CardioVR.Physiology;
using CardioVR.UI;

namespace CardioVR.Core
{
    /// Walks the trainee through the procedure one step at a time, deciding when
    /// each step's goal has been met.
    public class ProcedureRunner : MonoBehaviour
    {
        [SerializeField] ProcedureDefinition procedure;
        [SerializeField] CoaxialSystem coaxial;
        [SerializeField] FluoroscopyController fluoroscopy;
        [SerializeField] PatientVitals vitals;
        [SerializeField] PerformanceRecorder recorder;

        public int CurrentStepIndex { get; private set; } = -1;
        public ProcedureStep CurrentStep =>
            CurrentStepIndex >= 0 && CurrentStepIndex < procedure.steps.Count
                ? procedure.steps[CurrentStepIndex]
                : null;

        public bool IsComplete => CurrentStepIndex >= procedure.steps.Count;
        public float CurrentStepElapsed => Time.time - stepStartedAt;

        public event Action<ProcedureStep, int> StepStarted;
        public event Action<ProcedureStep, float> StepCompleted;
        public event Action ProcedureCompleted;

        float stepStartedAt;
        float contrastAtStepStart;
        bool arterialAccessObtained;
        bool hemostasisAchieved;

        void Start() => AdvanceToStep(0);

        void Update()
        {
            if (IsComplete || CurrentStep == null) return;
            if (IsGoalMet(CurrentStep)) CompleteCurrentStep();
        }

        bool IsGoalMet(ProcedureStep step)
        {
            switch (step.goal)
            {
                case StepGoal.ObtainArterialAccess:
                    return arterialAccessObtained;

                case StepGoal.AdvanceGuidewire:
                    return coaxial.Guidewire.State.TipSegmentId == step.targetSegmentId;

                case StepGoal.TrackCatheterOverWire:
                    // Reaching the target is not enough — it has to have been reached
                    // over the wire rather than by pushing a bare catheter up there.
                    return coaxial.Catheter.State.TipSegmentId == step.targetSegmentId
                           && coaxial.CatheterIsRailed;

                case StepGoal.WithdrawGuidewire:
                    return !coaxial.WireThroughTip;

                case StepGoal.AdvanceToSegment:
                case StepGoal.EngageOstium:
                    return coaxial.Catheter.State.TipSegmentId == step.targetSegmentId;

                case StepGoal.InjectContrast:
                    return fluoroscopy.ContrastUsedMl - contrastAtStepStart >= step.minContrastMl;

                case StepGoal.SetCArmProjection:
                    return fluoroscopy.MatchesProjection(
                        step.primaryAngleDegrees, step.secondaryAngleDegrees, step.angleToleranceDegrees);

                case StepGoal.WithdrawCatheter:
                    return coaxial.Catheter.State.TipSegmentId == procedure.vesselNetworkAccessId
                           && coaxial.Catheter.State.TipDepthCm <= 0.5f;

                case StepGoal.AchieveHemostasis:
                    return hemostasisAchieved;

                case StepGoal.TreatInstability:
                    return !vitals.IsUnstable;

                default:
                    return false;
            }
        }

        void CompleteCurrentStep()
        {
            float elapsed = CurrentStepElapsed;
            recorder.RecordStepCompleted(CurrentStep, elapsed);
            StepCompleted?.Invoke(CurrentStep, elapsed);
            AdvanceToStep(CurrentStepIndex + 1);
        }

        void AdvanceToStep(int index)
        {
            CurrentStepIndex = index;
            stepStartedAt = Time.time;
            contrastAtStepStart = fluoroscopy.ContrastUsedMl;

            if (IsComplete)
            {
                recorder.RecordProcedureCompleted();
                ProcedureCompleted?.Invoke();
                return;
            }

            recorder.RecordStepStarted(CurrentStep);
            StepStarted?.Invoke(CurrentStep, index);
        }

        public void NotifyArterialAccess() => arterialAccessObtained = true;
        public void NotifyHemostasis() => hemostasisAchieved = true;

        /// Lets the trainee move on when a step has no automatic completion cue,
        /// or when an instructor overrides. Recorded as a skip, not a pass.
        public void SkipCurrentStep()
        {
            if (IsComplete) return;
            recorder.RecordStepSkipped(CurrentStep);
            AdvanceToStep(CurrentStepIndex + 1);
        }
    }
}
