using UnityEngine;
using CardioVR.Interaction;

namespace CardioVR.Catheter
{
    /// Pumps operator input into the navigator and renders the resulting
    /// resistance back to the hand.
    [RequireComponent(typeof(CatheterNavigator))]
    public class CatheterInputDriver : MonoBehaviour
    {
        [SerializeField] MonoBehaviour inputSource;

        [Tooltip("Largest advance applied in a single physics step, in cm. Caps the "
                 + "effect of a dropped frame or a tracking glitch.")]
        [SerializeField] float maxStepCm = 2f;

        CatheterNavigator navigator;
        ICatheterInput input;

        void Awake()
        {
            navigator = GetComponent<CatheterNavigator>();
            input = inputSource as ICatheterInput;

            if (input == null)
                Debug.LogError($"{nameof(inputSource)} must implement {nameof(ICatheterInput)}.", this);
        }

        void FixedUpdate()
        {
            if (input == null) return;

            float advance = Mathf.Clamp(input.ConsumeAdvanceCm(), -maxStepCm, maxStepCm);
            float roll = input.ConsumeRollDegrees();

            if (advance != 0f || roll != 0f)
                navigator.Step(advance, roll);

            input.ApplyForceFeedback(navigator.State.Resistance);
        }
    }
}
