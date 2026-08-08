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

        [Tooltip("French size; outer diameter in mm = frenchSize / 3.")]
        public float frenchSize = 5f;

        [Range(0.2f, 3f)] public float shaftStiffness = 1f;

        [Tooltip("How faithfully tip rotation follows shaft torque. Higher is more forgiving at an ostium.")]
        [Range(0.5f, 3f)] public float torqueResponse = 1f;

        [Tooltip("Ostia this tip shape is designed to engage; engaging others is penalised at debrief.")]
        public string[] intendedOstiaIds = new string[0];

        public float OuterRadiusMm => frenchSize / 6f;
    }

    public enum CatheterKind
    {
        Guidewire,
        DiagnosticCoronary,
        GuideCatheter,
        Pigtail
    }
}
