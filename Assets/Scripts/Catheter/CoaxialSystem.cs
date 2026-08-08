using UnityEngine;
using CardioVR.Interaction;

namespace CardioVR.Catheter
{
    /// The guidewire and the catheter as one coaxial system, and the constraints
    /// between them.
    ///
    /// Nothing here is a checklist. The workflow a trainee is supposed to learn —
    /// wire first, track the catheter over it, pull the wire back before engaging —
    /// falls out of three pieces of geometry:
    ///
    ///   * While the wire leads, the catheter is *railed* to the wire's path. It
    ///     cannot choose a branch the wire did not take, so it cannot engage a
    ///     coronary until the wire is withdrawn behind its tip.
    ///   * Past the wire tip the catheter is bare. It steers freely again, but every
    ///     wall contact costs progressively more the further it runs unsupported.
    ///   * The wire is longer than the catheter by a fixed margin, so it can only
    ///     lead by that much before its back end vanishes into the hub and there is
    ///     nothing left to hold. Withdrawing the catheter eats the same margin.
    ///
    /// Do it in the wrong order and the simulator does not refuse — it lets you,
    /// and the vessel remembers.
    public class CoaxialSystem : MonoBehaviour
    {
        [SerializeField] CatheterNavigator guidewire;
        [SerializeField] CatheterNavigator catheter;

        [Tooltip("Must implement ICoaxialInput.")]
        [SerializeField] MonoBehaviour inputSource;

        [Header("Limits")]
        [Tooltip("Largest advance applied to one device in a single step, in cm.")]
        [SerializeField] float maxStepCm = 2f;

        [Tooltip("Wire that must stay outside the hub to remain holdable, in cm.")]
        [SerializeField] float minWireStubCm = 10f;

        [Tooltip("Shaft that must stay outside the sheath to remain holdable, in cm.")]
        [SerializeField] float minShaftStubCm = 5f;

        [Header("Support")]
        [Tooltip("Extra trauma per cm the catheter runs on past the wire tip. Unsupported "
                 + "catheter against a vessel wall is how dissections start.")]
        [SerializeField] float unsupportedTraumaPerCm = 0.35f;

        [Tooltip("Trauma while properly tracking over the wire. A supported catheter is gentle.")]
        [Range(0.1f, 1f)]
        [SerializeField] float railedTraumaScale = 0.5f;

        [Tooltip("Ceiling on the unsupported penalty. Once the wire is out entirely, working the "
                 + "catheter in the root is ordinary practice, not an escalating hazard — without "
                 + "this cap the penalty would keep growing with the wire's distance behind the tip. "
                 + "Needs clinical calibration.")]
        [Range(1f, 6f)]
        [SerializeField] float maxUnsupportedTraumaScale = 2.5f;

        ICoaxialInput input;

        public CatheterNavigator Guidewire => guidewire;
        public CatheterNavigator Catheter => catheter;

        public float WireInsertedCm => guidewire.InsertedLengthCm;
        public float CatheterInsertedCm => catheter.InsertedLengthCm;

        /// How far the wire tip is ahead of the catheter tip. Negative means the
        /// catheter has run on past it.
        public float WireLeadCm => WireInsertedCm - CatheterInsertedCm;

        public bool CatheterIsRailed => WireLeadCm > 0.01f;
        public float UnsupportedLengthCm => Mathf.Max(0f, -WireLeadCm);

        /// True while the wire still protrudes from the catheter tip — the state in
        /// which an ostium cannot be engaged and contrast should not be injected.
        public bool WireThroughTip => CatheterIsRailed;

        public float ExposedCatheterCm => catheter.Profile.totalLengthCm - CatheterInsertedCm;
        public float ExposedWireCm => guidewire.Profile.totalLengthCm - WireInsertedCm;

        /// Bare wire between the catheter hub and the wire's back end. Once this
        /// reaches zero there is nothing left to hold on to.
        public float WireStubCm => ExposedWireCm - ExposedCatheterCm;

        float MaxLeadCm =>
            guidewire.Profile.totalLengthCm - catheter.Profile.totalLengthCm - minWireStubCm;

        void Awake()
        {
            input = inputSource as ICoaxialInput;

            if (input == null)
                Debug.LogError($"{nameof(inputSource)} must implement {nameof(ICoaxialInput)}.", this);

            if (!guidewire.Profile.IsGuidewire)
                Debug.LogWarning("The guidewire slot is holding a profile that is not a guidewire.", this);

            if (guidewire.Profile.totalLengthCm <= catheter.Profile.totalLengthCm)
                Debug.LogError("The guidewire must be longer than the catheter, or it cannot be held "
                             + "while the catheter tracks over it.", this);
        }

        void OnEnable()
        {
            guidewire.Buckled += OnBuckled;
            catheter.Buckled += OnBuckled;
        }

        void OnDisable()
        {
            guidewire.Buckled -= OnBuckled;
            catheter.Buckled -= OnBuckled;
        }

        void OnBuckled(CatheterNavigator device)
            => input?.PulseOnBuckle(device == guidewire ? Device.Guidewire : Device.Catheter);

        void FixedUpdate()
        {
            if (input == null) return;

            input.UpdateGeometry(ExposedCatheterCm, ExposedWireCm);

            StepDevice(Device.Guidewire, guidewire);
            StepDevice(Device.Catheter, catheter);

            ApplySupport();

            input.ApplyForceFeedback(Device.Guidewire, guidewire.State.Resistance);
            input.ApplyForceFeedback(Device.Catheter, catheter.State.Resistance);
        }

        void StepDevice(Device device, CatheterNavigator navigator)
        {
            float advance = Mathf.Clamp(input.ConsumeAdvanceCm(device), -maxStepCm, maxStepCm);
            float roll = input.ConsumeRollDegrees(device);

            navigator.Stabilised = input.IsStabilised(device);
            advance = ClampAdvance(device, navigator, advance);

            if (advance != 0f || roll != 0f) navigator.Step(advance, roll);
        }

        /// Keeps both devices holdable: enough shaft outside the sheath to grip, and
        /// enough wire outside the hub that the wire has not been swallowed.
        float ClampAdvance(Device device, CatheterNavigator navigator, float advance)
        {
            if (advance <= 0f) return ClampWithdrawal(device, advance);

            float headroom = navigator.Profile.totalLengthCm - minShaftStubCm - navigator.InsertedLengthCm;
            advance = Mathf.Min(advance, Mathf.Max(0f, headroom));

            // Advancing the wire lengthens its lead and eats the stub outside the hub.
            if (device == Device.Guidewire)
                advance = Mathf.Min(advance, Mathf.Max(0f, MaxLeadCm - WireLeadCm));

            return advance;
        }

        float ClampWithdrawal(Device device, float advance)
        {
            // Pulling the catheter back brings the hub out toward the wire's back end,
            // consuming exactly the same margin that advancing the wire does. This is
            // why exchanging a catheter needs a longer wire than the one you came in on.
            if (device == Device.Catheter)
                advance = Mathf.Max(advance, -Mathf.Max(0f, MaxLeadCm - WireLeadCm));

            return advance;
        }

        void ApplySupport()
        {
            bool railed = CatheterIsRailed;

            catheter.Rail = railed ? guidewire.State.Path : null;
            catheter.TraumaScale = railed
                ? railedTraumaScale
                : Mathf.Min(1f + UnsupportedLengthCm * unsupportedTraumaPerCm, maxUnsupportedTraumaScale);

            // The wire is never railed to anything; it is what everything else rides on.
            guidewire.Rail = null;
            guidewire.TraumaScale = 1f;
        }
    }
}
