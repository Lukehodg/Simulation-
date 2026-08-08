using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;
using CardioVR.UI;

namespace CardioVR.Interaction
{
    /// A stored-projection button on the tableside control pad.
    ///
    /// Real labs keep the standard views on presets rather than dialling angles by
    /// hand, and the trainee should learn which view opens which segment — LAO
    /// cranial for the left main bifurcation, RAO caudal for the circumflex — not
    /// learn to scrub a slider until the vessel stops foreshortening.
    public class ProjectionPresetButton : XRSimpleInteractable
    {
        [SerializeField] FluoroscopyController fluoroscopy;

        [Tooltip("LAO positive, RAO negative.")]
        [SerializeField] float primaryAngle;

        [Tooltip("Cranial positive, caudal negative.")]
        [SerializeField] float secondaryAngle;

        [SerializeField] string label = "AP";

        [Header("Feedback")]
        [SerializeField] Renderer indicator;
        [SerializeField] Color idle = new Color(0.16f, 0.19f, 0.22f);
        [SerializeField] Color active = new Color(0.94f, 0.70f, 0.24f);

        Material indicatorMaterial;

        public string Label => label;

        protected override void Awake()
        {
            base.Awake();
            if (indicator != null) indicatorMaterial = indicator.material;
        }

        protected override void OnSelectEntered(SelectEnterEventArgs args)
        {
            base.OnSelectEntered(args);
            fluoroscopy.SetProjection(primaryAngle, secondaryAngle);
        }

        void Update()
        {
            if (indicatorMaterial == null) return;

            bool selected = fluoroscopy.MatchesProjection(primaryAngle, secondaryAngle, 4f);
            indicatorMaterial.color = selected ? active : idle;
        }

        public void Configure(FluoroscopyController controller, float primary, float secondary, string name)
        {
            fluoroscopy = controller;
            primaryAngle = primary;
            secondaryAngle = secondary;
            label = name;
        }
    }
}
