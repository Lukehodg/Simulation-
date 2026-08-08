using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.InputSystem.XR;
using UnityEngine.XR.Interaction.Toolkit;
using Unity.XR.CoreUtils;
using CardioVR.Assessment;
using CardioVR.Catheter;
using CardioVR.Complications;
using CardioVR.Core;
using CardioVR.Interaction;
using CardioVR.Physiology;
using CardioVR.UI;
using CardioVR.Vasculature;
using CardioVR.XR;

namespace CardioVR.EditorTools
{
    /// Builds the cath lab scene from the JSON datasets and wires every reference.
    ///
    /// The scene is generated rather than committed because a Unity scene file is a
    /// wall of GUIDs that no one can review — this way the room's layout is code you
    /// can read in a diff, and rebuilding after an anatomy change is one menu click.
    public static class CathLabBuilder
    {
        const string GeneratedFolder = "Assets/Generated";
        const string ScenePath = "Assets/Scenes/CathLab.unity";
        const string RadiographyLayer = "Radiography";

        [MenuItem("CardioVR/Build Cath Lab Scene")]
        public static void Build()
        {
            int radiographyLayer = EnsureLayer(RadiographyLayer);
            Directory.CreateDirectory(GeneratedFolder);
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            var assets = CreateAssets();
            BuildRoom();

            var anatomy = BuildAnatomy(assets, radiographyLayer);
            var simulation = BuildSimulation(assets, anatomy);
            var cArm = BuildCArm(assets, simulation, radiographyLayer);
            BuildPatientMonitor(simulation);
            var rig = BuildXRRig();
            BuildTableside(assets, simulation, anatomy, rig);

            Undo.ClearAll();
            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene, ScenePath);
            AssetDatabase.SaveAssets();

            Debug.Log($"Cath lab built at {ScenePath}. Assign the XRI Default Input Actions to the two "
                    + "controllers (Package Manager > XR Interaction Toolkit > Samples > Starter Assets), "
                    + "then enable OpenXR under Project Settings > XR Plug-in Management.");

            Selection.activeObject = cArm;
        }

        /* ------------------------------------------------------------------ assets */

        class Assets
        {
            public VesselNetwork Network;
            public ProcedureDefinition Procedure;
            public CatheterProfile Catheter;
            public CatheterProfile Guidewire;
            public Material Radiopaque;
            public Material FluoroDisplay;
            public Material MonitorScreen;
            public RenderTexture FluoroTarget;
        }

        static Assets CreateAssets()
        {
            var network = VesselNetworkLoader.FromJson(ReadScenario("aorto-coronary-tree.json"));
            SaveAsset(network, "VesselNetwork.asset");

            var procedure = ProcedureLoader.FromJson(ReadScenario("diagnostic-coronary-angiography.json"));
            SaveAsset(procedure, "Procedure.asset");

            var catheter = ScriptableObject.CreateInstance<CatheterProfile>();
            catheter.displayName = "Judkins Left 4.0";
            catheter.kind = CatheterKind.DiagnosticCoronary;
            catheter.frenchSize = 5f;
            catheter.totalLengthCm = 100f;
            catheter.intendedOstiaIds = new[] { "left_main" };
            catheter.traumaMultiplier = 1f;
            catheter.canEnterCoronaries = true;
            SaveAsset(catheter, "CatheterProfile.asset");

            // A 0.035" J-tip wire: soft, blunt, and 50 cm longer than the catheter so
            // its back end stays holdable while the catheter tracks over it.
            var guidewire = ScriptableObject.CreateInstance<CatheterProfile>();
            guidewire.displayName = "0.035\" J-tip guidewire";
            guidewire.kind = CatheterKind.Guidewire;
            guidewire.frenchSize = 2.7f;
            guidewire.totalLengthCm = 150f;
            guidewire.shaftStiffness = 0.35f;
            guidewire.torqueResponse = 0.6f;
            guidewire.traumaMultiplier = 0.2f;
            guidewire.canEnterCoronaries = false;
            SaveAsset(guidewire, "GuidewireProfile.asset");

            var target = new RenderTexture(1024, 1024, 24, RenderTextureFormat.ARGBHalf)
            {
                name = "FluoroTarget",
                antiAliasing = 2,
                filterMode = FilterMode.Bilinear
            };
            SaveAsset(target, "FluoroTarget.renderTexture");

            var radiopaque = new Material(Shader.Find("CardioVR/Radiopaque")) { name = "Radiopaque" };
            SaveAsset(radiopaque, "Radiopaque.mat");

            var display = new Material(Shader.Find("CardioVR/FluoroDisplay")) { name = "FluoroDisplay" };
            display.mainTexture = target;
            SaveAsset(display, "FluoroDisplay.mat");

            var monitor = new Material(Shader.Find("Unlit/Texture")) { name = "MonitorScreen" };
            SaveAsset(monitor, "MonitorScreen.mat");

            return new Assets
            {
                Network = network,
                Procedure = procedure,
                Catheter = catheter,
                Guidewire = guidewire,
                Radiopaque = radiopaque,
                FluoroDisplay = display,
                MonitorScreen = monitor,
                FluoroTarget = target
            };
        }

        static string ReadScenario(string file)
        {
            string path = Path.Combine("Assets/Scenarios", file);
            var asset = AssetDatabase.LoadAssetAtPath<TextAsset>(path);
            if (asset == null) throw new FileNotFoundException($"Missing scenario dataset at {path}.");
            return asset.text;
        }

        static void SaveAsset(Object asset, string fileName)
        {
            string path = Path.Combine(GeneratedFolder, fileName);
            AssetDatabase.DeleteAsset(path);
            AssetDatabase.CreateAsset(asset, path);
        }

        /* ------------------------------------------------------------------- room */

        static void BuildRoom()
        {
            var floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
            floor.name = "Floor";
            floor.transform.localScale = new Vector3(0.8f, 1f, 0.8f);

            var table = GameObject.CreatePrimitive(PrimitiveType.Cube);
            table.name = "Table";
            table.transform.localScale = new Vector3(0.6f, 0.06f, 2.1f);
            table.transform.position = new Vector3(0f, 0.95f, 0f);

            // The patient, lying supine, feet toward +Z. The heart sits at the origin
            // of the anatomy space so the isocentre has somewhere honest to be.
            var patient = GameObject.CreatePrimitive(PrimitiveType.Capsule);
            patient.name = "Patient";
            patient.transform.localScale = new Vector3(0.42f, 0.85f, 0.28f);
            patient.transform.position = new Vector3(0f, 1.09f, 0f);
            patient.transform.rotation = Quaternion.Euler(90f, 0f, 0f);

            var drape = GameObject.CreatePrimitive(PrimitiveType.Cube);
            drape.name = "SterileDrape";
            drape.transform.localScale = new Vector3(0.7f, 0.01f, 1.2f);
            drape.transform.position = new Vector3(0f, 1.18f, 0.5f);

            var light = new GameObject("Room Light").AddComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 0.9f;
            light.transform.rotation = Quaternion.Euler(52f, -30f, 0f);
        }

        /* ---------------------------------------------------------------- anatomy */

        class Anatomy
        {
            public Transform Root;
            public Transform Sheath;
            public GameObject CatheterMesh;
            public GameObject GuidewireMesh;
        }

        static Anatomy BuildAnatomy(Assets assets, int layer)
        {
            // Vessel data is in centimetres; the root converts it to Unity metres.
            var root = new GameObject("Anatomy").transform;
            root.position = new Vector3(0f, 1.12f, 0f);
            root.localScale = Vector3.one * 0.01f;

            var vessels = new GameObject("Vessels").transform;
            vessels.SetParent(root, false);
            VesselMeshBuilder.BuildTree(assets.Network, vessels, assets.Radiopaque, layer);

            var catheterMesh = DeviceMesh("CatheterMesh", root, assets.Radiopaque, layer);
            var guidewireMesh = DeviceMesh("GuidewireMesh", root, assets.Radiopaque, layer);

            // Right femoral puncture, on the patient's right, shaft pointing up and
            // out of the groin toward the operator.
            var sheath = new GameObject("FemoralSheath").transform;
            sheath.position = new Vector3(-0.12f, 1.16f, 0.42f);
            sheath.rotation = Quaternion.LookRotation(new Vector3(-0.35f, 0.55f, 0.75f).normalized, Vector3.up);

            return new Anatomy
            {
                Root = root,
                Sheath = sheath,
                CatheterMesh = catheterMesh,
                GuidewireMesh = guidewireMesh
            };
        }

        static GameObject DeviceMesh(string name, Transform parent, Material material, int layer)
        {
            var go = new GameObject(name) { layer = layer };
            go.transform.SetParent(parent, false);
            go.AddComponent<MeshFilter>();

            var renderer = go.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = material;
            renderer.shadowCastingMode = ShadowCastingMode.Off;
            renderer.receiveShadows = false;
            return go;
        }

        /* ------------------------------------------------------------- simulation */

        class Simulation
        {
            public CoaxialSystem Coaxial;
            public PatientVitals Vitals;
            public ComplicationSystem Complications;
            public FluoroscopyController Fluoroscopy;
            public PerformanceRecorder Recorder;
            public ProcedureRunner Runner;
        }

        static Simulation BuildSimulation(Assets assets, Anatomy anatomy)
        {
            var go = new GameObject("Simulation");

            var catheterNav = BuildDevice(go.transform, anatomy.Root, "Catheter", assets.Network, assets.Catheter);
            var guidewireNav = BuildDevice(go.transform, anatomy.Root, "Guidewire", assets.Network, assets.Guidewire);

            var coaxial = go.AddComponent<CoaxialSystem>();
            Wire(coaxial, so =>
            {
                so.FindProperty("guidewire").objectReferenceValue = guidewireNav;
                so.FindProperty("catheter").objectReferenceValue = catheterNav;
            });

            var vitals = go.AddComponent<PatientVitals>();

            var complications = go.AddComponent<ComplicationSystem>();
            Wire(complications, so =>
            {
                so.FindProperty("coaxial").objectReferenceValue = coaxial;
                so.FindProperty("vitals").objectReferenceValue = vitals;
            });

            var fluoroscopy = go.AddComponent<FluoroscopyController>();
            Wire(fluoroscopy, so => so.FindProperty("complications").objectReferenceValue = complications);

            var recorder = go.AddComponent<PerformanceRecorder>();
            Wire(recorder, so => so.FindProperty("complications").objectReferenceValue = complications);

            var runner = go.AddComponent<ProcedureRunner>();
            Wire(runner, so =>
            {
                so.FindProperty("procedure").objectReferenceValue = assets.Procedure;
                so.FindProperty("coaxial").objectReferenceValue = coaxial;
                so.FindProperty("fluoroscopy").objectReferenceValue = fluoroscopy;
                so.FindProperty("vitals").objectReferenceValue = vitals;
                so.FindProperty("recorder").objectReferenceValue = recorder;
            });

            var debrief = go.AddComponent<DebriefReport>();
            Wire(debrief, so =>
            {
                so.FindProperty("procedure").objectReferenceValue = assets.Procedure;
                so.FindProperty("recorder").objectReferenceValue = recorder;
                so.FindProperty("fluoroscopy").objectReferenceValue = fluoroscopy;
                so.FindProperty("complications").objectReferenceValue = complications;
            });

            AttachRenderer(anatomy.CatheterMesh, catheterNav, assets.Catheter);
            AttachRenderer(anatomy.GuidewireMesh, guidewireNav, assets.Guidewire);

            return new Simulation
            {
                Coaxial = coaxial,
                Vitals = vitals,
                Complications = complications,
                Fluoroscopy = fluoroscopy,
                Recorder = recorder,
                Runner = runner
            };
        }

        static CatheterNavigator BuildDevice(
            Transform parent, Transform anatomyRoot, string name,
            VesselNetwork network, CatheterProfile profile)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);

            var tip = new GameObject(name + "Tip").transform;
            tip.SetParent(anatomyRoot, false);

            var navigator = go.AddComponent<CatheterNavigator>();
            Wire(navigator, so =>
            {
                so.FindProperty("network").objectReferenceValue = network;
                so.FindProperty("profile").objectReferenceValue = profile;
                so.FindProperty("tipMarker").objectReferenceValue = tip;
            });

            return navigator;
        }

        static void AttachRenderer(GameObject mesh, CatheterNavigator navigator, CatheterProfile profile)
        {
            var renderer = mesh.AddComponent<CatheterRenderer>();
            Wire(renderer, so =>
            {
                so.FindProperty("navigator").objectReferenceValue = navigator;
                so.FindProperty("profile").objectReferenceValue = profile;
            });
        }

        /* ------------------------------------------------------------------ C-arm */

        static GameObject BuildCArm(Assets assets, Simulation simulation, int radiographyLayer)
        {
            var rig = new GameObject("C-Arm");

            var isocentre = new GameObject("Isocentre").transform;
            isocentre.SetParent(rig.transform, false);
            isocentre.position = new Vector3(0f, 1.12f, 0f);

            var gantry = new GameObject("Gantry").transform;
            gantry.SetParent(rig.transform, false);
            gantry.position = isocentre.position;

            var detector = GameObject.CreatePrimitive(PrimitiveType.Cube);
            detector.name = "ImageIntensifier";
            detector.transform.SetParent(gantry, false);
            detector.transform.localPosition = new Vector3(0f, 0f, -0.55f);
            detector.transform.localScale = new Vector3(0.34f, 0.34f, 0.12f);

            var tube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            tube.name = "XRayTube";
            tube.transform.SetParent(gantry, false);
            tube.transform.localPosition = new Vector3(0f, 0f, 0.55f);
            tube.transform.localScale = new Vector3(0.3f, 0.3f, 0.18f);

            var camera = new GameObject("FluoroCamera").AddComponent<Camera>();
            camera.transform.SetParent(rig.transform, false);
            camera.cullingMask = 1 << radiographyLayer;
            camera.orthographic = true;
            camera.nearClipPlane = 0.01f;
            camera.farClipPlane = 3f;
            camera.targetTexture = assets.FluoroTarget;

            var monitor = GameObject.CreatePrimitive(PrimitiveType.Quad);
            monitor.name = "FluoroMonitor";
            monitor.transform.position = new Vector3(0.95f, 1.75f, -0.9f);
            monitor.transform.rotation = Quaternion.Euler(0f, 205f, 0f);
            monitor.transform.localScale = new Vector3(0.62f, 0.62f, 1f);
            monitor.GetComponent<Renderer>().sharedMaterial = assets.FluoroDisplay;

            var cArm = rig.AddComponent<CArmRig>();
            Wire(cArm, so =>
            {
                so.FindProperty("fluoroscopy").objectReferenceValue = simulation.Fluoroscopy;
                so.FindProperty("isocentre").objectReferenceValue = isocentre;
                so.FindProperty("gantry").objectReferenceValue = gantry;
                so.FindProperty("fluoroCamera").objectReferenceValue = camera;
                so.FindProperty("monitorSurface").objectReferenceValue = monitor.GetComponent<Renderer>();
                so.FindProperty("target").objectReferenceValue = assets.FluoroTarget;
            });

            return rig;
        }

        static void BuildPatientMonitor(Simulation simulation)
        {
            var screen = GameObject.CreatePrimitive(PrimitiveType.Quad);
            screen.name = "PatientMonitor";
            screen.transform.position = new Vector3(0.95f, 1.75f, -0.05f);
            screen.transform.rotation = Quaternion.Euler(0f, 245f, 0f);
            screen.transform.localScale = new Vector3(0.5f, 0.28f, 1f);

            var renderer = screen.AddComponent<PatientMonitorRenderer>();
            Wire(renderer, so =>
            {
                so.FindProperty("vitals").objectReferenceValue = simulation.Vitals;
                so.FindProperty("monitorSurface").objectReferenceValue = screen.GetComponent<Renderer>();
            });
        }

        /* -------------------------------------------------------------------- rig */

        static XROrigin BuildXRRig()
        {
            var originObject = new GameObject("XR Origin");
            var origin = originObject.AddComponent<XROrigin>();

            var offset = new GameObject("Camera Offset").transform;
            offset.SetParent(originObject.transform, false);

            var camera = new GameObject("Main Camera").AddComponent<Camera>();
            camera.tag = "MainCamera";
            camera.transform.SetParent(offset, false);
            camera.nearClipPlane = 0.02f;
            camera.AddComponent<AudioListener>();
            camera.gameObject.AddComponent<TrackedPoseDriver>();

            origin.Camera = camera;
            origin.CameraFloorOffsetObject = offset.gameObject;

            var manager = new GameObject("XR Interaction Manager").AddComponent<XRInteractionManager>();

            CreateHand(offset, "Left Controller", manager);
            CreateHand(offset, "Right Controller", manager);

            var bootstrapObject = new GameObject("XR Session");
            var standing = new GameObject("Operating Position").transform;
            standing.position = new Vector3(-0.75f, 0f, 0.55f);
            standing.rotation = Quaternion.Euler(0f, 35f, 0f);

            var bootstrap = bootstrapObject.AddComponent<XRSessionBootstrap>();
            Wire(bootstrap, so =>
            {
                so.FindProperty("origin").objectReferenceValue = origin;
                so.FindProperty("operatingPosition").objectReferenceValue = standing;
            });

            return origin;
        }

        static void CreateHand(Transform parent, string name, XRInteractionManager manager)
        {
            var hand = new GameObject(name);
            hand.transform.SetParent(parent, false);

            hand.AddComponent<ActionBasedController>();

            var grab = hand.AddComponent<SphereCollider>();
            grab.isTrigger = true;
            grab.radius = 0.06f;

            var interactor = hand.AddComponent<XRDirectInteractor>();
            interactor.interactionManager = manager;
        }

        /* -------------------------------------------------------------- tableside */

        static void BuildTableside(Assets assets, Simulation simulation, Anatomy anatomy, XROrigin origin)
        {
            // The coaxial shaft: catheter from the sheath to the hub, bare wire beyond
            // it. Two colliders, because which one you grab is what decides which
            // device you are moving.
            var shaft = new GameObject("CoaxialShaft");

            var catheterCollider = ShaftSegmentCollider(shaft.transform, "CatheterShaft");
            var wireCollider = ShaftSegmentCollider(shaft.transform, "GuidewireStub");

            var shaftInteractable = shaft.AddComponent<CoaxialShaftInteractable>();
            Wire(shaftInteractable, so =>
            {
                so.FindProperty("sheath").objectReferenceValue = anatomy.Sheath;
                so.FindProperty("catheterCollider").objectReferenceValue = catheterCollider;
                so.FindProperty("wireCollider").objectReferenceValue = wireCollider;
            });

            Wire(simulation.Coaxial,
                 so => so.FindProperty("inputSource").objectReferenceValue = shaftInteractable);

            // Screening pedal on the floor, operator side.
            var pedalObject = GameObject.CreatePrimitive(PrimitiveType.Cube);
            pedalObject.name = "FluoroPedal";
            pedalObject.transform.position = new Vector3(-0.75f, 0.05f, 0.55f);
            pedalObject.transform.localScale = new Vector3(0.26f, 0.09f, 0.2f);

            var volume = pedalObject.AddComponent<BoxCollider>();
            volume.isTrigger = true;
            volume.size = new Vector3(1f, 3f, 1f);

            var pedal = pedalObject.AddComponent<FluoroPedal>();
            Wire(pedal, so =>
            {
                so.FindProperty("fluoroscopy").objectReferenceValue = simulation.Fluoroscopy;
                so.FindProperty("pedalVolume").objectReferenceValue = volume;
                so.FindProperty("pedalPlate").objectReferenceValue = pedalObject.transform;
            });

            // Contrast syringe on the table.
            var syringeObject = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            syringeObject.name = "ContrastSyringe";
            syringeObject.transform.position = new Vector3(-0.38f, 1.22f, 0.9f);
            syringeObject.transform.localScale = new Vector3(0.035f, 0.08f, 0.035f);

            var body = syringeObject.AddComponent<Rigidbody>();
            body.useGravity = false;
            body.isKinematic = true;

            var plunger = new GameObject("Plunger").transform;
            plunger.SetParent(syringeObject.transform, false);

            var syringe = syringeObject.AddComponent<ContrastSyringe>();
            Wire(syringe, so =>
            {
                so.FindProperty("fluoroscopy").objectReferenceValue = simulation.Fluoroscopy;
                so.FindProperty("plunger").objectReferenceValue = plunger;
            });

            BuildProjectionPad(simulation);
        }

        static CapsuleCollider ShaftSegmentCollider(Transform parent, string name)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);

            var capsule = go.AddComponent<CapsuleCollider>();
            capsule.isTrigger = true;
            capsule.direction = 2;
            return capsule;
        }

        /// The stored projections a lab keeps on its control pad. These four cover
        /// the standard diagnostic run.
        static readonly (string Label, float Primary, float Secondary)[] Projections =
        {
            ("AP", 0f, 0f),
            ("LAO 40 / CRA 20", 40f, 20f),
            ("RAO 30 / CAU 20", -30f, -20f),
            ("LAO 45 / CAU 25", 45f, -25f)
        };

        static void BuildProjectionPad(Simulation simulation)
        {
            var pad = new GameObject("ProjectionPad").transform;
            pad.position = new Vector3(-0.44f, 1.22f, -0.35f);
            pad.rotation = Quaternion.Euler(20f, 30f, 0f);

            for (int i = 0; i < Projections.Length; i++)
            {
                var (label, primary, secondary) = Projections[i];

                var button = GameObject.CreatePrimitive(PrimitiveType.Cube);
                button.name = "Preset_" + label;
                button.transform.SetParent(pad, false);
                button.transform.localPosition = new Vector3(i * 0.06f - 0.09f, 0f, 0f);
                button.transform.localScale = new Vector3(0.05f, 0.012f, 0.05f);

                var collider = button.GetComponent<BoxCollider>();
                collider.isTrigger = true;

                var preset = button.AddComponent<ProjectionPresetButton>();
                preset.Configure(simulation.Fluoroscopy, primary, secondary, label);

                Wire(preset, so =>
                {
                    so.FindProperty("fluoroscopy").objectReferenceValue = simulation.Fluoroscopy;
                    so.FindProperty("primaryAngle").floatValue = primary;
                    so.FindProperty("secondaryAngle").floatValue = secondary;
                    so.FindProperty("label").stringValue = label;
                    so.FindProperty("indicator").objectReferenceValue = button.GetComponent<Renderer>();
                });
            }
        }

        /* ----------------------------------------------------------------- helpers */

        static void Wire(Object target, System.Action<SerializedObject> configure)
        {
            var so = new SerializedObject(target);
            configure(so);
            so.ApplyModifiedPropertiesWithoutUndo();
        }

        /// Adds a layer to the project if it is not already there, and returns its index.
        static int EnsureLayer(string name)
        {
            int existing = LayerMask.NameToLayer(name);
            if (existing >= 0) return existing;

            var tagManager = new SerializedObject(
                AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/TagManager.asset")[0]);
            var layers = tagManager.FindProperty("layers");

            // 0-7 are Unity's own; user layers start at 8.
            for (int i = 8; i < layers.arraySize; i++)
            {
                var entry = layers.GetArrayElementAtIndex(i);
                if (!string.IsNullOrEmpty(entry.stringValue)) continue;

                entry.stringValue = name;
                tagManager.ApplyModifiedPropertiesWithoutUndo();
                return i;
            }

            throw new System.InvalidOperationException($"No free user layer for '{name}'.");
        }
    }
}
