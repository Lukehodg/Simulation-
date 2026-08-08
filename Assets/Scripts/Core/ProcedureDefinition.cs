using System;
using System.Collections.Generic;
using UnityEngine;

namespace CardioVR.Core
{
    public enum StepGoal
    {
        ObtainArterialAccess,
        AdvanceToSegment,
        EngageOstium,
        InjectContrast,
        SetCArmProjection,
        WithdrawCatheter,
        AchieveHemostasis,
        TreatInstability
    }

    [Serializable]
    public class ProcedureStep
    {
        public string title;
        [TextArea(2, 4)] public string instruction;
        public StepGoal goal;

        [Tooltip("Vessel the tip must reach, for AdvanceToSegment and EngageOstium.")]
        public string targetSegmentId;

        [Tooltip("Contrast required for this step, in ml.")]
        public float minContrastMl = 6f;

        public float primaryAngleDegrees;
        public float secondaryAngleDegrees;
        public float angleToleranceDegrees = 12f;

        [Tooltip("Seconds after which the step is flagged as slow at debrief. Zero disables.")]
        public float parTimeSeconds = 90f;

        [TextArea(1, 3)] public string hint;
    }

    [CreateAssetMenu(menuName = "CardioVR/Procedure", fileName = "Procedure")]
    public class ProcedureDefinition : ScriptableObject
    {
        public string displayName = "Diagnostic Coronary Angiography";
        [TextArea(3, 6)] public string briefing;

        public string vesselNetworkAccessId = "femoral_r";
        public List<ProcedureStep> steps = new List<ProcedureStep>();

        [Header("Debrief targets")]
        public float targetFluoroSeconds = 240f;
        public float targetContrastMl = 70f;
        public float targetDoseMilliGray = 600f;
    }
}
