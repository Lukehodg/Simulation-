using System;
using UnityEngine;
using CardioVR.Vasculature;

namespace CardioVR.Catheter
{
    /// Advances the catheter tip through the vessel tree from operator input and
    /// reports the axial resistance that input should feel.
    public class CatheterNavigator : MonoBehaviour
    {
        [SerializeField] VesselNetwork network;
        [SerializeField] CatheterProfile profile;
        [SerializeField] Transform tipMarker;

        [Header("Mechanics")]
        [SerializeField] float baseFrictionPerCm = 0.012f;
        [SerializeField] float tortuosityGain = 3.5f;
        [SerializeField] float buckleResistance = 9f;

        public CatheterState State { get; } = new CatheterState();
        public VesselNetwork Network => network;
        public VesselSegment TipSegment => network.Get(State.TipSegmentId);

        public event Action<VesselSegment> SegmentEntered;
        public event Action<VesselSegment, float> WallContact;
        public event Action Buckled;

        void Awake()
        {
            network.Bake();
            State.ResetTo(network.accessSegmentId);
            UpdateTipPose();
        }

        public void Step(float advanceCm, float rollDeltaDegrees)
        {
            State.RollDegrees = Mathf.Repeat(State.RollDegrees + rollDeltaDegrees, 360f);

            bool wasBuckling = State.IsBuckling;
            State.IsBuckling = false;

            if (advanceCm > 0f) Advance(advanceCm);
            else if (advanceCm < 0f) Withdraw(-advanceCm);

            State.Resistance = ComputeResistance(advanceCm);
            UpdateTipPose();

            if (State.IsBuckling && !wasBuckling) Buckled?.Invoke();

            if (advanceCm != 0f)
            {
                var tip = network.Get(State.TipSegmentId);
                float clearance = tip.LumenRadiusAt(State.TipDepthCm) - profile.OuterRadiusMm;
                if (clearance < 0f) WallContact?.Invoke(tip, -clearance);
            }
        }

        void Advance(float remaining)
        {
            // Guard against a pathological input producing an unbounded branch walk.
            for (int guard = 0; guard < 32 && remaining > 0f; guard++)
            {
                var current = network.Get(State.TipSegmentId);
                float target = State.TipDepthCm + remaining;

                if (TryEnterBranch(current, State.TipDepthCm, target, out var child, out float branchCm))
                {
                    remaining -= branchCm - State.TipDepthCm;
                    State.Path.Add(child.id);
                    State.TipDepthCm = 0f;
                    SegmentEntered?.Invoke(child);
                    continue;
                }

                if (target <= current.LengthCm)
                {
                    State.TipDepthCm = target;
                    return;
                }

                // Distal end with no branch the operator is aligned with: the
                // catheter stops and the extra push goes into buckling the shaft.
                State.TipDepthCm = current.LengthCm;
                State.IsBuckling = true;
                return;
            }
        }

        bool TryEnterBranch(VesselSegment current, float fromCm, float toCm, out VesselSegment entered, out float branchCm)
        {
            foreach (var child in network.ChildrenOf(current.id))
            {
                float cm = child.branchPointOnParent * current.LengthCm;

                // Inclusive at fromCm so a tip already parked at the ostium can
                // engage once the operator torques into alignment.
                if (cm < fromCm || cm > toCm) continue;
                if (!IsRollAligned(child)) continue;
                if (profile.OuterRadiusMm > child.lumenRadiusMm) continue;

                entered = child;
                branchCm = cm;
                return true;
            }

            entered = null;
            branchCm = 0f;
            return false;
        }

        bool IsRollAligned(VesselSegment child)
        {
            float delta = Mathf.Abs(Mathf.DeltaAngle(State.RollDegrees, child.ostiumRollDegrees));
            return delta <= child.ostiumRollToleranceDegrees * profile.torqueResponse;
        }

        void Withdraw(float remaining)
        {
            while (remaining > 0f)
            {
                if (State.TipDepthCm >= remaining)
                {
                    State.TipDepthCm -= remaining;
                    return;
                }

                remaining -= State.TipDepthCm;

                if (State.Path.Count == 1)
                {
                    State.TipDepthCm = 0f;
                    return;
                }

                var child = network.Get(State.TipSegmentId);
                State.Path.RemoveAt(State.Path.Count - 1);
                var parent = network.Get(State.TipSegmentId);
                State.TipDepthCm = child.branchPointOnParent * parent.LengthCm;
            }
        }

        float ComputeResistance(float advanceCm)
        {
            if (State.IsBuckling) return buckleResistance * profile.shaftStiffness;

            float length = State.InsertedLengthCm(network);
            float friction = length * baseFrictionPerCm;

            float tortuosityLoad = 0f;
            foreach (var id in State.Path)
                tortuosityLoad += network.Get(id).tortuosity;
            friction += tortuosityLoad * tortuosityGain * profile.shaftStiffness;

            var tip = network.Get(State.TipSegmentId);
            float clearance = tip.LumenRadiusAt(State.TipDepthCm) - profile.OuterRadiusMm;
            if (clearance < 0f) friction += -clearance * 6f;

            return advanceCm >= 0f ? friction : friction * 0.75f;
        }

        void UpdateTipPose()
        {
            var tip = network.Get(State.TipSegmentId);
            State.TipPosition = tip.PointAt(State.TipDepthCm);
            State.TipTangent = tip.TangentAt(State.TipDepthCm);

            if (tipMarker != null)
            {
                tipMarker.localPosition = State.TipPosition;
                tipMarker.localRotation = Quaternion.LookRotation(State.TipTangent);
            }
        }
    }
}
