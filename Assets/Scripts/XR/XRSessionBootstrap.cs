using UnityEngine;
using UnityEngine.XR;
using Unity.XR.CoreUtils;

namespace CardioVR.XR
{
    /// Session-level XR settings for a standing, room-scale procedure.
    ///
    /// There is no locomotion in this simulator: the trainee stands at the table
    /// where they would stand in the lab and the anatomy comes to them. That single
    /// decision removes the usual source of simulator sickness, so the comfort work
    /// here is about holding frame rate rather than about vignettes and teleports.
    public class XRSessionBootstrap : MonoBehaviour
    {
        [SerializeField] XROrigin origin;

        [Header("Performance")]
        [Tooltip("Standalone headsets hold 72 or 90 Hz. Missing it in a procedure sim is worse than a softer image.")]
        [SerializeField] int targetFrameRate = 72;

        [Tooltip("Render scale. Drop below 1 before dropping frames.")]
        [Range(0.6f, 1.4f)]
        [SerializeField] float renderScale = 1f;

        [Header("Tableside")]
        [Tooltip("Where the operator should be standing — the right femoral side of the table.")]
        [SerializeField] Transform operatingPosition;

        void Awake()
        {
            if (origin != null)
                origin.RequestedTrackingOriginMode = XROrigin.TrackingOriginMode.Floor;

            Application.targetFrameRate = targetFrameRate;
            QualitySettings.vSyncCount = 0;
            XRSettings.eyeTextureResolutionScale = renderScale;
        }

        void Start()
        {
            if (operatingPosition != null) MoveToTableside();
        }

        /// Re-seats the trainee at the table without moving the world — used on
        /// start and whenever they ask to recentre after stepping away.
        public void MoveToTableside()
        {
            if (origin == null || operatingPosition == null) return;

            origin.MoveCameraToWorldLocation(operatingPosition.position);
            origin.MatchOriginUpCameraForward(operatingPosition.up, operatingPosition.forward);
        }
    }
}
