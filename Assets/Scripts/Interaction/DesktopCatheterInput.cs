using UnityEngine;

namespace CardioVR.Interaction
{
    /// Keyboard stand-in so the simulation can be developed and tested without a
    /// headset. W/S advance and withdraw, A/D torque the shaft.
    public class DesktopCatheterInput : MonoBehaviour, ICatheterInput
    {
        [SerializeField] float advanceCmPerSecond = 6f;
        [SerializeField] float rollDegreesPerSecond = 90f;

        float pendingAdvanceCm;
        float pendingRollDegrees;

        void Update()
        {
            float advance = 0f;
            if (Input.GetKey(KeyCode.W)) advance += 1f;
            if (Input.GetKey(KeyCode.S)) advance -= 1f;

            float roll = 0f;
            if (Input.GetKey(KeyCode.D)) roll += 1f;
            if (Input.GetKey(KeyCode.A)) roll -= 1f;

            pendingAdvanceCm += advance * advanceCmPerSecond * Time.deltaTime;
            pendingRollDegrees += roll * rollDegreesPerSecond * Time.deltaTime;
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

        public void ApplyForceFeedback(float newtons) { }
    }
}
