using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;

namespace CardioVR.Interaction
{
    /// Where axial resistance gets rendered back to the operator.
    ///
    /// Controller rumble is a coarse stand-in for catheter force feedback — it
    /// conveys *that* resistance rose, not how much. This seam exists so a real
    /// haptic catheter interface can replace it without touching the simulation.
    public interface IHapticDriver
    {
        void RenderResistance(float newtons);
        void Pulse(float amplitude, float seconds);
    }

    public class NullHapticDriver : IHapticDriver
    {
        public void RenderResistance(float newtons) { }
        public void Pulse(float amplitude, float seconds) { }
    }

    /// Renders resistance as a continuous low-amplitude buzz on whichever
    /// controllers are gripping, re-issued each frame because XR controllers only
    /// accept fire-and-forget impulses.
    public class XRControllerHaptics : IHapticDriver
    {
        readonly XRBaseController[] controllers;
        readonly float saturationNewtons;
        readonly float floor;

        public XRControllerHaptics(XRBaseController[] controllers, float saturationNewtons = 10f, float floor = 0.04f)
        {
            this.controllers = controllers;
            this.saturationNewtons = Mathf.Max(0.01f, saturationNewtons);
            this.floor = floor;
        }

        public void RenderResistance(float newtons)
        {
            float amplitude = Mathf.Clamp01(newtons / saturationNewtons);
            if (amplitude < floor) return;

            // Slightly longer than a frame so the buzz reads as continuous rather
            // than as a stutter when the frame rate dips.
            Pulse(amplitude, Mathf.Max(Time.deltaTime * 1.5f, 0.02f));
        }

        public void Pulse(float amplitude, float seconds)
        {
            foreach (var controller in controllers)
                if (controller != null)
                    controller.SendHapticImpulse(Mathf.Clamp01(amplitude), seconds);
        }
    }
}
