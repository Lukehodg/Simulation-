using UnityEngine;

namespace CardioVR.Interaction
{
    /// Keyboard stand-in so the simulation can be developed and tested without a
    /// headset. W/S advance and withdraw, A/D torque, Tab switches between the
    /// guidewire and the catheter.
    ///
    /// The Tab key is the one place this diverges from the headset build, where
    /// device selection is a consequence of where you take hold rather than a mode.
    public class DesktopCoaxialInput : MonoBehaviour, ICoaxialInput
    {
        [SerializeField] float advanceCmPerSecond = 6f;
        [SerializeField] float rollDegreesPerSecond = 90f;

        readonly float[] pendingAdvanceCm = new float[2];
        readonly float[] pendingRollDegrees = new float[2];

        public Device ActiveDevice { get; private set; } = Device.Guidewire;

        void Update()
        {
            if (Input.GetKeyDown(KeyCode.Tab))
                ActiveDevice = ActiveDevice == Device.Guidewire ? Device.Catheter : Device.Guidewire;

            float advance = 0f;
            if (Input.GetKey(KeyCode.W)) advance += 1f;
            if (Input.GetKey(KeyCode.S)) advance -= 1f;

            float roll = 0f;
            if (Input.GetKey(KeyCode.D)) roll += 1f;
            if (Input.GetKey(KeyCode.A)) roll -= 1f;

            int index = (int)ActiveDevice;
            pendingAdvanceCm[index] += advance * advanceCmPerSecond * Time.deltaTime;
            pendingRollDegrees[index] += roll * rollDegreesPerSecond * Time.deltaTime;
        }

        public float ConsumeAdvanceCm(Device device)
        {
            float value = pendingAdvanceCm[(int)device];
            pendingAdvanceCm[(int)device] = 0f;
            return value;
        }

        public float ConsumeRollDegrees(Device device)
        {
            float value = pendingRollDegrees[(int)device];
            pendingRollDegrees[(int)device] = 0f;
            return value;
        }

        public bool IsStabilised(Device device) => false;
        public void ApplyForceFeedback(Device device, float newtons) { }
        public void PulseOnBuckle(Device device) { }
        public void UpdateGeometry(float exposedCatheterCm, float exposedWireCm) { }
    }
}
