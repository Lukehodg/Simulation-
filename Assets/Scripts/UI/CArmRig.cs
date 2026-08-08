using UnityEngine;

namespace CardioVR.UI
{
    /// Drives the physical C-arm and the camera that renders the fluoroscopic image.
    ///
    /// The gantry orbits the isocentre — a point parked in the mediastinum — so the
    /// projection the trainee sees is the projection the geometry actually produces.
    /// LAO/RAO swings the arm about the patient's long axis, CRA/CAU tilts it toward
    /// the head or the feet.
    public class CArmRig : MonoBehaviour
    {
        [SerializeField] FluoroscopyController fluoroscopy;

        [Header("Rig")]
        [Tooltip("Point the gantry orbits — place it at the heart.")]
        [SerializeField] Transform isocentre;

        [Tooltip("Rotated by the primary and secondary angles. The detector and tube hang from this.")]
        [SerializeField] Transform gantry;

        [SerializeField] Camera fluoroCamera;

        [Header("Optics")]
        [Tooltip("Distance from isocentre to the image intensifier, in metres.")]
        [SerializeField] float detectorDistance = 0.55f;

        [Tooltip("Field of view across the intensifier, in metres. Smaller magnifies and raises dose.")]
        [SerializeField] float fieldSizeMetres = 0.18f;

        [SerializeField] float slewDegreesPerSecond = 45f;

        [Header("Display")]
        [SerializeField] Renderer monitorSurface;
        [SerializeField] RenderTexture target;

        static readonly int LiveId = Shader.PropertyToID("_Live");
        static readonly int GrainId = Shader.PropertyToID("_Grain");

        Material monitorMaterial;
        float currentPrimary;
        float currentSecondary;

        void Awake()
        {
            if (fluoroCamera != null)
            {
                fluoroCamera.orthographic = true;
                fluoroCamera.orthographicSize = fieldSizeMetres * 0.5f;
                fluoroCamera.clearFlags = CameraClearFlags.SolidColor;
                fluoroCamera.backgroundColor = Color.black;
                fluoroCamera.targetTexture = target;
            }

            if (monitorSurface != null)
            {
                monitorMaterial = monitorSurface.material;
                if (target != null) monitorMaterial.mainTexture = target;
            }
        }

        void LateUpdate()
        {
            // The gantry has mass; it does not snap between projections.
            float maxStep = slewDegreesPerSecond * Time.deltaTime;
            currentPrimary = Mathf.MoveTowards(currentPrimary, fluoroscopy.PrimaryAngleDegrees, maxStep);
            currentSecondary = Mathf.MoveTowards(currentSecondary, fluoroscopy.SecondaryAngleDegrees, maxStep);

            var rotation = Quaternion.Euler(currentSecondary, currentPrimary, 0f);

            if (gantry != null)
            {
                gantry.SetPositionAndRotation(isocentre.position, rotation);
            }

            if (fluoroCamera != null)
            {
                // Looking from the detector back through the isocentre.
                Vector3 offset = rotation * Vector3.back * detectorDistance;
                fluoroCamera.transform.SetPositionAndRotation(
                    isocentre.position + offset,
                    Quaternion.LookRotation(isocentre.position - (isocentre.position + offset), rotation * Vector3.up));

                fluoroCamera.orthographicSize = fieldSizeMetres * 0.5f;
                fluoroCamera.enabled = fluoroscopy.IsScreening;
            }

            if (monitorMaterial != null)
            {
                monitorMaterial.SetFloat(LiveId, fluoroscopy.IsScreening ? 1f : 0f);

                // Noise falls as cumulative dose rises: more photons, cleaner image.
                float dosePerSecond = fluoroscopy.FluoroSeconds > 0.5f
                    ? fluoroscopy.CumulativeDoseMilliGray / fluoroscopy.FluoroSeconds
                    : 1.8f;
                monitorMaterial.SetFloat(GrainId, Mathf.Lerp(0.09f, 0.02f, Mathf.InverseLerp(1f, 4f, dosePerSecond)));
            }
        }

        /// Magnification: a tighter field is easier to read and costs more dose.
        public void SetFieldSize(float metres) => fieldSizeMetres = Mathf.Clamp(metres, 0.10f, 0.30f);
    }
}
