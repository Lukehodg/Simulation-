using System;
using System.Collections.Generic;
using UnityEngine;

namespace CardioVR.Core
{
    /// Reads a procedure script from JSON.
    ///
    /// Goes through a string-typed DTO because Unity's JsonUtility cannot parse an
    /// enum written as a name, and the scenario files are meant to stay readable to
    /// the clinicians who edit them — "EngageOstium", not 3.
    public static class ProcedureLoader
    {
        [Serializable]
        class StepDto
        {
            public string title;
            public string instruction;
            public string goal;
            public string targetSegmentId;
            public float minContrastMl;
            public float primaryAngleDegrees;
            public float secondaryAngleDegrees;
            public float angleToleranceDegrees;
            public float parTimeSeconds;
            public string hint;
        }

        [Serializable]
        class ProcedureDto
        {
            public string displayName;
            public string briefing;
            public string vesselNetworkAccessId;
            public float targetFluoroSeconds;
            public float targetContrastMl;
            public float targetDoseMilliGray;
            public List<StepDto> steps;
        }

        public static ProcedureDefinition FromJson(string json)
        {
            var dto = JsonUtility.FromJson<ProcedureDto>(json);
            if (dto?.steps == null || dto.steps.Count == 0)
                throw new ArgumentException("Procedure JSON contained no steps.");

            var procedure = ScriptableObject.CreateInstance<ProcedureDefinition>();
            procedure.displayName = dto.displayName;
            procedure.briefing = dto.briefing;
            procedure.vesselNetworkAccessId = dto.vesselNetworkAccessId;
            procedure.targetFluoroSeconds = dto.targetFluoroSeconds;
            procedure.targetContrastMl = dto.targetContrastMl;
            procedure.targetDoseMilliGray = dto.targetDoseMilliGray;
            procedure.steps = new List<ProcedureStep>(dto.steps.Count);

            foreach (var step in dto.steps)
            {
                if (!Enum.TryParse(step.goal, out StepGoal goal))
                    throw new ArgumentException($"Step '{step.title}' has unknown goal '{step.goal}'.");

                procedure.steps.Add(new ProcedureStep
                {
                    title = step.title,
                    instruction = step.instruction,
                    goal = goal,
                    targetSegmentId = step.targetSegmentId,
                    minContrastMl = step.minContrastMl,
                    primaryAngleDegrees = step.primaryAngleDegrees,
                    secondaryAngleDegrees = step.secondaryAngleDegrees,
                    angleToleranceDegrees = step.angleToleranceDegrees > 0f ? step.angleToleranceDegrees : 12f,
                    parTimeSeconds = step.parTimeSeconds,
                    hint = step.hint
                });
            }

            return procedure;
        }

        public static ProcedureDefinition FromResource(string resourcePath)
        {
            var asset = Resources.Load<TextAsset>(resourcePath);
            if (asset == null)
                throw new ArgumentException($"No procedure at Resources/{resourcePath}.");

            return FromJson(asset.text);
        }
    }
}
