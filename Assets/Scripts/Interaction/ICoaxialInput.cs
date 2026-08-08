namespace CardioVR.Interaction
{
    public enum Device
    {
        Guidewire,
        Catheter
    }

    /// Operator intent for both devices in the coaxial system. Implemented by the
    /// VR shaft and by a desktop stand-in so the simulation runs without a headset.
    public interface ICoaxialInput
    {
        /// Centimetres to advance (positive) or withdraw (negative) this frame.
        float ConsumeAdvanceCm(Device device);

        /// Degrees of torque applied to that device this frame.
        float ConsumeRollDegrees(Device device);

        /// Whether a second hand is steadying that device near the sheath.
        bool IsStabilised(Device device);

        /// Axial resistance to render back to the operator, in newtons.
        void ApplyForceFeedback(Device device, float newtons);

        /// The shaft has buckled — a sharp, unmistakable cue rather than a graded one.
        void PulseOnBuckle(Device device);

        /// How much of each device is currently outside the patient, in cm. The VR
        /// shaft needs this to know where the hub is; the desktop stand-in ignores it.
        void UpdateGeometry(float exposedCatheterCm, float exposedWireCm);
    }
}
