using System.Collections.Generic;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;
using CardioVR.Catheter;

namespace CardioVR.Interaction
{
    /// The catheter shaft as a grabbable object, driven the way a real one is.
    ///
    /// The shaft protrudes from the femoral sheath. You grip it somewhere along
    /// the exposed length and push toward the sheath to advance, twist about its
    /// axis to torque. Because your grip is a fixed point *on the shaft*, pushing
    /// carries your hand toward the sheath — and when it arrives you have run out
    /// of travel and must release and re-grip further back, exactly as in the lab.
    /// That constraint is the reason this belongs in VR rather than on a gamepad.
    ///
    /// A second hand on the shaft stabilises it: the tip tracks torque more
    /// faithfully and the shaft is far less likely to buckle under load.
    [RequireComponent(typeof(CapsuleCollider))]
    public class CatheterShaftInteractable : XRBaseInteractable, ICatheterInput
    {
        [Header("Anatomy hookup")]
        [Tooltip("Entry point on the patient. Forward points out of the body, along the exposed shaft.")]
        [SerializeField] Transform sheath;
        [SerializeField] CatheterNavigator navigator;

        [Header("Shaft")]
        [Tooltip("Usable shaft length in centimetres. Once it is all inside, you cannot advance further.")]
        [SerializeField] float totalShaftLengthCm = 110f;
        [SerializeField] float shaftRadiusMetres = 0.004f;

        [Header("Motion mapping")]
        [Tooltip("1 = one-to-one: a metre of hand travel inserts 100 cm of catheter.")]
        [SerializeField] float motionScale = 1f;

        [Tooltip("Hand travel below this is treated as tracking jitter, in metres.")]
        [SerializeField] float deadZoneMetres = 0.0012f;

        [Header("Feedback")]
        [SerializeField] float hapticSaturationNewtons = 10f;

        class Grip
        {
            public Transform hand;
            public XRBaseController controller;
            public float offsetCm;          // distance from the sheath to the grip point
            public Vector3 lastPosition;
            public Quaternion lastRotation;
            public bool hasPrevious;
        }

        readonly Dictionary<IXRSelectInteractor, Grip> grips = new Dictionary<IXRSelectInteractor, Grip>();
        readonly List<XRBaseController> hapticTargets = new List<XRBaseController>();

        IXRSelectInteractor driver;
        CapsuleCollider shaftCollider;
        IHapticDriver haptics = new NullHapticDriver();

        float pendingAdvanceCm;
        float pendingRollDegrees;

        public int HandsOnShaft => grips.Count;
        public bool IsStabilised => grips.Count > 1;
        public float ExposedLengthCm { get; private set; }

        protected override void Awake()
        {
            base.Awake();
            selectMode = InteractableSelectMode.Multiple;
            shaftCollider = GetComponent<CapsuleCollider>();
            ExposedLengthCm = totalShaftLengthCm;
        }

        protected override void OnSelectEntered(SelectEnterEventArgs args)
        {
            base.OnSelectEntered(args);

            var hand = args.interactorObject.transform;
            var grip = new Grip
            {
                hand = hand,
                controller = (args.interactorObject as XRBaseControllerInteractor)?.xrController,
                offsetCm = GripOffsetFor(hand.position),
                lastPosition = hand.position,
                lastRotation = hand.rotation,
                hasPrevious = true
            };

            grips[args.interactorObject] = grip;
            driver = args.interactorObject;
            RebuildHaptics();
        }

        protected override void OnSelectExited(SelectExitEventArgs args)
        {
            base.OnSelectExited(args);

            grips.Remove(args.interactorObject);

            if (driver == args.interactorObject)
            {
                driver = null;
                foreach (var key in grips.Keys) { driver = key; break; }
            }

            RebuildHaptics();
        }

        void RebuildHaptics()
        {
            hapticTargets.Clear();
            foreach (var grip in grips.Values)
                if (grip.controller != null) hapticTargets.Add(grip.controller);

            haptics = hapticTargets.Count > 0
                ? new XRControllerHaptics(hapticTargets.ToArray(), hapticSaturationNewtons)
                : (IHapticDriver)new NullHapticDriver();
        }

        /// Where along the exposed shaft a hand has taken hold, measured from the sheath.
        float GripOffsetFor(Vector3 handPosition)
        {
            float alongMetres = Vector3.Dot(handPosition - sheath.position, sheath.forward);
            return Mathf.Clamp(alongMetres * 100f, 0f, ExposedLengthCm);
        }

        public override void ProcessInteractable(XRInteractionUpdateOrder.UpdatePhase updatePhase)
        {
            base.ProcessInteractable(updatePhase);
            if (updatePhase != XRInteractionUpdateOrder.UpdatePhase.Dynamic) return;

            ExposedLengthCm = Mathf.Max(0f, totalShaftLengthCm - navigator.State.InsertedLengthCm(navigator.Network));
            UpdateShaftCollider();

            foreach (var grip in grips.Values) AccumulateFrom(grip, grip == GripFor(driver));
        }

        Grip GripFor(IXRSelectInteractor interactor)
            => interactor != null && grips.TryGetValue(interactor, out var g) ? g : null;

        void AccumulateFrom(Grip grip, bool isDriver)
        {
            Vector3 position = grip.hand.position;
            Quaternion rotation = grip.hand.rotation;

            if (!grip.hasPrevious)
            {
                grip.lastPosition = position;
                grip.lastRotation = rotation;
                grip.hasPrevious = true;
                return;
            }

            Vector3 axis = sheath.forward;
            Vector3 delta = position - grip.lastPosition;

            // Pushing toward the body is motion against the outward shaft axis.
            float towardBodyMetres = -Vector3.Dot(delta, axis);

            if (Mathf.Abs(towardBodyMetres) > deadZoneMetres && isDriver)
            {
                float requestedCm = towardBodyMetres * 100f * motionScale;

                // Travel is bounded by the grip: you cannot push past the sheath,
                // and you cannot pull back further than the shaft you have left.
                float advanceCm = requestedCm > 0f
                    ? Mathf.Min(requestedCm, grip.offsetCm)
                    : Mathf.Max(requestedCm, -(ExposedLengthCm - grip.offsetCm));

                grip.offsetCm = Mathf.Clamp(grip.offsetCm - advanceCm, 0f, Mathf.Max(ExposedLengthCm, 0.01f));
                pendingAdvanceCm += advanceCm;
            }

            // Torque applied by either hand counts; a stabilising second hand makes
            // the tip follow the shaft more faithfully.
            Quaternion twist = rotation * Quaternion.Inverse(grip.lastRotation);
            twist.ToAngleAxis(out float angle, out Vector3 twistAxis);
            if (angle > 180f) angle -= 360f;
            if (!float.IsNaN(angle) && twistAxis.sqrMagnitude > 0.0001f)
                pendingRollDegrees += angle * Vector3.Dot(twistAxis.normalized, axis);

            grip.lastPosition = position;
            grip.lastRotation = rotation;
        }

        void UpdateShaftCollider()
        {
            float lengthMetres = Mathf.Max(ExposedLengthCm, 1f) / 100f;

            transform.SetPositionAndRotation(
                sheath.position + sheath.forward * (lengthMetres * 0.5f),
                sheath.rotation);

            shaftCollider.direction = 2;             // local Z, matching the sheath's forward
            shaftCollider.height = lengthMetres;
            shaftCollider.radius = shaftRadiusMetres;
            shaftCollider.center = Vector3.zero;
        }

        /* ---- ICatheterInput ---- */

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
            if (grips.Count == 0) return;

            // A stabilised shaft transmits less of the load back to the hands.
            haptics.RenderResistance(IsStabilised ? newtons * 0.6f : newtons);
        }

        public void PulseOnBuckle() => haptics.Pulse(0.9f, 0.12f);
    }
}
