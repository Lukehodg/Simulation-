using System.Collections.Generic;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;

namespace CardioVR.Interaction
{
    /// The coaxial shaft protruding from the sheath: catheter nearest the patient,
    /// then the hub, then the bare guidewire running out past it.
    ///
    /// That layout is not a UI decision — it is where the two devices physically
    /// are. Which means **where you take hold decides what you move**: grip between
    /// the sheath and the hub and you are moving the catheter; grip beyond the hub
    /// and you are moving the wire. There is no mode to select and nothing to
    /// toggle, exactly as at a real table.
    ///
    /// Both devices inherit the same constraint as before: your grip is a fixed
    /// point on the shaft, so pushing carries your hand toward the sheath — or
    /// toward the hub, for the wire — and running out of travel means releasing and
    /// re-gripping further back.
    public class CoaxialShaftInteractable : XRBaseInteractable, ICoaxialInput
    {
        [Header("Anatomy hookup")]
        [Tooltip("Entry point on the patient. Forward points out of the body, along the exposed shaft.")]
        [SerializeField] Transform sheath;

        [Header("Motion mapping")]
        [Tooltip("1 = one-to-one: a metre of hand travel inserts 100 cm of device.")]
        [SerializeField] float motionScale = 1f;

        [Tooltip("Hand travel below this is treated as tracking jitter, in metres.")]
        [SerializeField] float deadZoneMetres = 0.0012f;

        [Header("Geometry")]
        [SerializeField] CapsuleCollider catheterCollider;
        [SerializeField] CapsuleCollider wireCollider;
        [SerializeField] float catheterRadiusMetres = 0.004f;
        [SerializeField] float wireRadiusMetres = 0.0015f;

        [Header("Feedback")]
        [SerializeField] float hapticSaturationNewtons = 10f;

        class Grip
        {
            public Device Device;
            public Transform Hand;
            public XRBaseController Controller;
            public float OffsetCm;
            public Vector3 LastPosition;
            public Quaternion LastRotation;
        }

        readonly Dictionary<IXRSelectInteractor, Grip> grips = new Dictionary<IXRSelectInteractor, Grip>();
        readonly float[] pendingAdvanceCm = new float[2];
        readonly float[] pendingRollDegrees = new float[2];
        readonly List<XRBaseController> hapticScratch = new List<XRBaseController>();

        IXRSelectInteractor[] drivers = new IXRSelectInteractor[2];

        float exposedCatheterCm = 100f;
        float exposedWireCm = 150f;

        public int HandsOn(Device device)
        {
            int count = 0;
            foreach (var grip in grips.Values) if (grip.Device == device) count++;
            return count;
        }

        protected override void Awake()
        {
            base.Awake();
            selectMode = InteractableSelectMode.Multiple;
        }

        /// Pushed in by the coaxial system each frame — this component owns the
        /// operator's hands, not the state of the devices.
        public void UpdateGeometry(float exposedCatheter, float exposedWire)
        {
            exposedCatheterCm = Mathf.Max(0f, exposedCatheter);
            exposedWireCm = Mathf.Max(exposedCatheterCm, exposedWire);
            ResizeColliders();
        }

        protected override void OnSelectEntered(SelectEnterEventArgs args)
        {
            base.OnSelectEntered(args);

            var hand = args.interactorObject.transform;
            float offset = OffsetAlongShaftCm(hand.position);

            // Past the hub is bare wire; before it, catheter.
            var device = offset > exposedCatheterCm ? Device.Guidewire : Device.Catheter;

            grips[args.interactorObject] = new Grip
            {
                Device = device,
                Hand = hand,
                Controller = (args.interactorObject as XRBaseControllerInteractor)?.xrController,
                OffsetCm = offset,
                LastPosition = hand.position,
                LastRotation = hand.rotation
            };

            drivers[(int)device] = args.interactorObject;
        }

        protected override void OnSelectExited(SelectExitEventArgs args)
        {
            base.OnSelectExited(args);

            if (!grips.TryGetValue(args.interactorObject, out var leaving)) return;
            grips.Remove(args.interactorObject);

            if (drivers[(int)leaving.Device] != args.interactorObject) return;

            // Hand the wheel to any remaining hand on that same device.
            drivers[(int)leaving.Device] = null;
            foreach (var pair in grips)
                if (pair.Value.Device == leaving.Device) { drivers[(int)leaving.Device] = pair.Key; break; }
        }

        float OffsetAlongShaftCm(Vector3 handPosition)
        {
            float metres = Vector3.Dot(handPosition - sheath.position, sheath.forward);
            return Mathf.Clamp(metres * 100f, 0f, exposedWireCm);
        }

        public override void ProcessInteractable(XRInteractionUpdateOrder.UpdatePhase updatePhase)
        {
            base.ProcessInteractable(updatePhase);
            if (updatePhase != XRInteractionUpdateOrder.UpdatePhase.Dynamic) return;

            foreach (var pair in grips)
                Accumulate(pair.Value, drivers[(int)pair.Value.Device] == pair.Key);
        }

        void Accumulate(Grip grip, bool isDriver)
        {
            Vector3 position = grip.Hand.position;
            Quaternion rotation = grip.Hand.rotation;
            Vector3 axis = sheath.forward;

            float towardBodyMetres = -Vector3.Dot(position - grip.LastPosition, axis);

            if (isDriver && Mathf.Abs(towardBodyMetres) > deadZoneMetres)
            {
                float requestedCm = towardBodyMetres * 100f * motionScale;

                // The catheter runs out of travel at the sheath, the wire at the hub.
                float floorCm = grip.Device == Device.Guidewire ? exposedCatheterCm : 0f;
                float ceilingCm = grip.Device == Device.Guidewire ? exposedWireCm : exposedCatheterCm;

                float advanceCm = requestedCm > 0f
                    ? Mathf.Min(requestedCm, grip.OffsetCm - floorCm)
                    : Mathf.Max(requestedCm, grip.OffsetCm - ceilingCm);

                grip.OffsetCm = Mathf.Clamp(grip.OffsetCm - advanceCm, floorCm, Mathf.Max(ceilingCm, floorCm));
                pendingAdvanceCm[(int)grip.Device] += advanceCm;
            }

            Quaternion twist = rotation * Quaternion.Inverse(grip.LastRotation);
            twist.ToAngleAxis(out float angle, out Vector3 twistAxis);
            if (angle > 180f) angle -= 360f;
            if (!float.IsNaN(angle) && twistAxis.sqrMagnitude > 0.0001f)
                pendingRollDegrees[(int)grip.Device] += angle * Vector3.Dot(twistAxis.normalized, axis);

            grip.LastPosition = position;
            grip.LastRotation = rotation;
        }

        void ResizeColliders()
        {
            if (catheterCollider != null)
            {
                float lengthMetres = Mathf.Max(exposedCatheterCm, 1f) / 100f;
                catheterCollider.transform.SetPositionAndRotation(
                    sheath.position + sheath.forward * (lengthMetres * 0.5f), sheath.rotation);
                catheterCollider.direction = 2;
                catheterCollider.height = lengthMetres;
                catheterCollider.radius = catheterRadiusMetres;
            }

            if (wireCollider != null)
            {
                float stubMetres = Mathf.Max(exposedWireCm - exposedCatheterCm, 0.5f) / 100f;
                float midCm = (exposedWireCm + exposedCatheterCm) * 0.5f;
                wireCollider.transform.SetPositionAndRotation(
                    sheath.position + sheath.forward * (midCm / 100f), sheath.rotation);
                wireCollider.direction = 2;
                wireCollider.height = stubMetres;
                wireCollider.radius = wireRadiusMetres;
            }
        }

        /* ---- ICoaxialInput ---- */

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

        public bool IsStabilised(Device device) => HandsOn(device) > 1;

        public void ApplyForceFeedback(Device device, float newtons)
        {
            hapticScratch.Clear();
            foreach (var grip in grips.Values)
                if (grip.Device == device && grip.Controller != null) hapticScratch.Add(grip.Controller);

            if (hapticScratch.Count == 0) return;

            float scaled = IsStabilised(device) ? newtons * 0.6f : newtons;
            new XRControllerHaptics(hapticScratch.ToArray(), hapticSaturationNewtons).RenderResistance(scaled);
        }

        public void PulseOnBuckle(Device device)
        {
            hapticScratch.Clear();
            foreach (var grip in grips.Values)
                if (grip.Device == device && grip.Controller != null) hapticScratch.Add(grip.Controller);

            if (hapticScratch.Count == 0) return;
            new XRControllerHaptics(hapticScratch.ToArray(), hapticSaturationNewtons).Pulse(0.9f, 0.12f);
        }
    }
}
