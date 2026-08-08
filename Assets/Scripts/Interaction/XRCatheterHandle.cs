using UnityEngine;
using UnityEngine.XR;

namespace CardioVR.Interaction
{
    /// Maps a tracked controller gripping the catheter hub to insertion and torque.
    ///
    /// Motion along the shaft axis becomes advance/withdraw; rotation about that
    /// axis becomes roll. This mirrors how the real thing is handled — the trainee
    /// pushes and torques rather than pressing a stick — which is the whole reason
    /// to do this in VR.
    ///
    /// Drive <see cref="IsGripped"/> from your XR grab interactable (XRI's
    /// selectEntered/selectExited, or an equivalent), and point
    /// <see cref="handTransform"/> at the interactor.
    public class XRCatheterHandle : MonoBehaviour, ICatheterInput
    {
        [SerializeField] Transform handTransform;
        [SerializeField] Transform shaftAxis;
        [SerializeField] XRNode hapticNode = XRNode.RightHand;

        [Tooltip("Metres of hand travel per centimetre of catheter advance.")]
        [SerializeField] float translationScale = 0.01f;

        [Tooltip("Resistance in newtons that saturates the haptic actuator.")]
        [SerializeField] float maxFeedbackNewtons = 10f;

        [Tooltip("Below this, small tracking jitter is ignored.")]
        [SerializeField] float deadZoneMetres = 0.0015f;

        public bool IsGripped { get; set; }

        float pendingAdvanceCm;
        float pendingRollDegrees;
        Vector3 lastHandPosition;
        Quaternion lastHandRotation;
        bool hasPrevious;

        void Update()
        {
            if (!IsGripped)
            {
                hasPrevious = false;
                return;
            }

            Vector3 position = handTransform.position;
            Quaternion rotation = handTransform.rotation;

            if (hasPrevious)
            {
                Vector3 axis = shaftAxis.forward;
                Vector3 delta = position - lastHandPosition;

                float along = Vector3.Dot(delta, axis);
                if (Mathf.Abs(along) > deadZoneMetres)
                    pendingAdvanceCm += along / translationScale;

                Quaternion twist = rotation * Quaternion.Inverse(lastHandRotation);
                twist.ToAngleAxis(out float angle, out Vector3 twistAxis);
                if (angle > 180f) angle -= 360f;
                pendingRollDegrees += angle * Vector3.Dot(twistAxis.normalized, axis);
            }

            lastHandPosition = position;
            lastHandRotation = rotation;
            hasPrevious = true;
        }

        public float ConsumeAdvanceCm()
        {
            float value = pendingAdvanceCm;
            pendingAdvanceCm = 0f;
            return value;
        }

        public float ConsumeRollDegrees()
        {
            float value = pendingRollDegrees;
            pendingRollDegrees = 0f;
            return value;
        }

        public void ApplyForceFeedback(float newtons)
        {
            if (!IsGripped) return;

            var device = InputDevices.GetDeviceAtXRNode(hapticNode);
            if (!device.isValid) return;

            float amplitude = Mathf.Clamp01(newtons / maxFeedbackNewtons);
            if (amplitude < 0.05f) return;

            device.SendHapticImpulse(0u, amplitude, Time.deltaTime);
        }
    }
}
