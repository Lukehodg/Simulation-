using UnityEngine;

namespace CardioVR.Catheter
{
    /// A selectable catheter or guidewire. French size is the classic sizing unit:
    /// 1 Fr = 1/3 mm of outer diameter.
    [CreateAssetMenu(menuName = "CardioVR/Catheter Profile", fileName = "CatheterProfile")]
    public class CatheterProfile : ScriptableObject
    {
        public string displayName = "Judkins Left 4.0";
        public CatheterKind kind = CatheterKind.DiagnosticCoronary;

        [Tooltip("French size; outer diameter in mm = frenchSize / 3. A 0.035\" guidewire is about 2.7 Fr.")]
        public float frenchSize = 5f;

        [Tooltip("Usable length in cm. A wire must be longer than the catheter it rides in, "
                 + "or its back end disappears into the hub and becomes unholdable.")]
        public float totalLengthCm = 100f;

        [Range(0.2f, 3f)] public float shaftStiffness = 1f;

        [Tooltip("How faithfully tip rotation follows shaft torque. Higher is more forgiving at an ostium.")]
        [Range(0.5f, 3f)] public float torqueResponse = 1f;

        [Tooltip("Ostia this tip shape is designed to engage; engaging others is penalised at debrief.")]
        public string[] intendedOstiaIds = new string[0];

        [Header("Handling")]
        [Tooltip("How much wall contact this device costs relative to a diagnostic catheter. "
                 + "A soft J-tip wire is roughly a fifth as traumatic, which is the whole "
                 + "reason it leads and the catheter follows.")]
        [Range(0.05f, 2f)] public float traumaMultiplier = 1f;

        [Tooltip("Whether this tip can select a coronary ostium. A 0.035\" J-wire cannot — "
                 + "it is far too large and blunt, so the catheter must engage on its own.")]
        public bool canEnterCoronaries = true;

        public float OuterRadiusMm => frenchSize / 6f;
        public bool IsGuidewire => kind == CatheterKind.Guidewire;
    }

    public enum CatheterKind
    {
        Guidewire,
        DiagnosticCoronary,
        GuideCatheter,
        Pigtail
    }
}
