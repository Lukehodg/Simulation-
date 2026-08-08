using UnityEngine;
using UnityEngine.InputSystem;
using CardioVR.UI;

namespace CardioVR.Interaction
{
    /// The screening pedal.
    ///
    /// In a real lab this is under your foot, and that matters: screening is a
    /// deliberate act with a cost, and it leaves your hands on the catheter. A
    /// headset gives you no feet, so this binds three ways and any of them works:
    ///
    ///   1. an actual USB foot pedal, through the input action below — the honest option;
    ///   2. a tracked foot or controller pressed onto the pedal collider on the floor;
    ///   3. a controller button, as the fallback when neither is available.
    ///
    /// Whichever is used, screening stops the moment it is released. Nothing here
    /// latches, because a latched beam is how dose runs away.
    public class FluoroPedal : MonoBehaviour
    {
        [SerializeField] FluoroscopyController fluoroscopy;

        [Header("Bindings")]
        [Tooltip("Foot pedal peripheral or controller button. Any non-zero value screens.")]
        [SerializeField] InputActionProperty pedalAction;

        [Tooltip("Colliders entering this trigger also press the pedal.")]
        [SerializeField] Collider pedalVolume;

        [SerializeField] LayerMask pressedBy = ~0;

        [Header("Travel")]
        [SerializeField] Transform pedalPlate;
        [SerializeField] float plateTravelMetres = 0.02f;

        int contacts;

        public bool IsPressed { get; private set; }

        void OnEnable() => pedalAction.action?.Enable();
        void OnDisable()
        {
            pedalAction.action?.Disable();
            contacts = 0;
            Release();
        }

        void Update()
        {
            bool actionPressed = pedalAction.action != null && pedalAction.action.IsPressed();
            bool pressed = actionPressed || contacts > 0;

            if (pressed != IsPressed)
            {
                IsPressed = pressed;
                fluoroscopy.SetPedal(pressed);
            }

            if (pedalPlate != null)
            {
                float target = IsPressed ? -plateTravelMetres : 0f;
                var local = pedalPlate.localPosition;
                local.y = Mathf.MoveTowards(local.y, target, 0.12f * Time.deltaTime);
                pedalPlate.localPosition = local;
            }
        }

        void OnTriggerEnter(Collider other)
        {
            if ((pressedBy.value & (1 << other.gameObject.layer)) == 0) return;
            contacts++;
        }

        void OnTriggerExit(Collider other)
        {
            if ((pressedBy.value & (1 << other.gameObject.layer)) == 0) return;
            contacts = Mathf.Max(0, contacts - 1);
        }

        void Release()
        {
            if (!IsPressed) return;
            IsPressed = false;
            fluoroscopy.SetPedal(false);
        }
    }
}
