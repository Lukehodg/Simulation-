using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;
using CardioVR.UI;

namespace CardioVR.Interaction
{
    /// The contrast syringe: pick it up, squeeze to inject.
    ///
    /// Flow follows how hard the trigger is squeezed, so the trainee has to commit
    /// to a short firm injection rather than tapping a button. The plunger travels
    /// with the squeeze and the barrel empties, which is the only reminder that
    /// contrast is a finite, kidney-taxing resource.
    public class ContrastSyringe : XRGrabInteractable
    {
        [Header("Injector")]
        [SerializeField] FluoroscopyController fluoroscopy;
        [SerializeField] float barrelVolumeMl = 10f;

        [Tooltip("Trigger pull below this does not break stiction in the plunger.")]
        [SerializeField] float breakawayThreshold = 0.15f;

        [Header("Plunger")]
        [SerializeField] Transform plunger;
        [SerializeField] float plungerTravelMetres = 0.06f;

        [Header("Purging")]
        [Tooltip("Air left in the line causes an embolism. Purge by injecting to waste before the first run.")]
        [SerializeField] bool startsWithAir = true;

        float remainingMl;
        float squeeze;
        bool injecting;

        protected override void Awake()
        {
            base.Awake();
            remainingMl = barrelVolumeMl;
            if (fluoroscopy != null) fluoroscopy.LineIsPurged = !startsWithAir;
        }

        public float RemainingMl => remainingMl;

        public override void ProcessInteractable(XRInteractionUpdateOrder.UpdatePhase updatePhase)
        {
            base.ProcessInteractable(updatePhase);
            if (updatePhase != XRInteractionUpdateOrder.UpdatePhase.Dynamic) return;

            squeeze = ReadSqueeze();
            float effective = Mathf.InverseLerp(breakawayThreshold, 1f, squeeze);

            if (effective > 0f && remainingMl > 0f)
            {
                if (!injecting)
                {
                    injecting = true;
                    fluoroscopy.BeginInjection();
                }

                fluoroscopy.HoldInjection();
                remainingMl = Mathf.Max(0f, remainingMl - fluoroscopy.ActiveInjectionMl * Time.deltaTime);
            }
            else if (injecting)
            {
                injecting = false;
                fluoroscopy.EndInjection();
            }

            if (plunger != null)
            {
                float depleted = 1f - remainingMl / barrelVolumeMl;
                plunger.localPosition = new Vector3(0f, 0f, -depleted * plungerTravelMetres);
            }
        }

        float ReadSqueeze()
        {
            float highest = 0f;

            foreach (var interactor in interactorsSelecting)
            {
                var controller = (interactor as XRBaseControllerInteractor)?.xrController;
                if (controller == null) continue;

                highest = Mathf.Max(highest, controller.activateInteractionState.value);
            }

            return highest;
        }

        protected override void OnSelectExited(SelectExitEventArgs args)
        {
            base.OnSelectExited(args);

            if (injecting)
            {
                injecting = false;
                fluoroscopy.EndInjection();
            }
        }

        /// Refill between runs at the table.
        public void Refill()
        {
            remainingMl = barrelVolumeMl;
            fluoroscopy.LineIsPurged = true;
        }
    }
}
