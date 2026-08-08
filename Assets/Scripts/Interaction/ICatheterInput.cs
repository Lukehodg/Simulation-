namespace CardioVR.Interaction
{
    /// Source of operator intent for the catheter. Implemented by the VR handle
    /// and by a desktop stand-in so the simulation runs without a headset.
    public interface ICatheterInput
    {
        /// Centimetres to advance (positive) or withdraw (negative) this frame.
        float ConsumeAdvanceCm();

        /// Degrees of shaft torque applied this frame.
        float ConsumeRollDegrees();

        /// Axial resistance to render back to the operator, in newtons.
        void ApplyForceFeedback(float newtons);
    }
}
