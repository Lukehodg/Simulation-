// Density accumulation for the fluoroscopy camera.
//
// Everything radiopaque renders additively into a black target, so overlapping
// structures sum the way absorption does along a beam path. Grazing angles
// traverse more material, which is why a contrast-filled tube reads dense at its
// edges and lighter down the middle.
//
// The display pass inverts this into a radiograph. Written in CG for the widest
// pipeline compatibility; it renders correctly under URP as an unlit pass.
Shader "CardioVR/Radiopaque"
{
    Properties
    {
        _Density ("Density", Range(0, 4)) = 1
        _EdgeGain ("Edge gain", Range(0, 3)) = 1.4
        _Floor ("Base absorption", Range(0, 1)) = 0.25
    }

    SubShader
    {
        Tags { "Queue" = "Transparent" "RenderType" = "Transparent" "IgnoreProjector" = "True" }

        Blend One One
        ZWrite Off
        ZTest LEqual
        Cull Off

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            float _Density;
            float _EdgeGain;
            float _Floor;

            struct appdata
            {
                float4 vertex : POSITION;
                float3 normal : NORMAL;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float3 worldNormal : TEXCOORD0;
                float3 viewDir : TEXCOORD1;
            };

            v2f vert(appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.worldNormal = UnityObjectToWorldNormal(v.normal);
                o.viewDir = WorldSpaceViewDir(v.vertex);
                return o;
            }

            fixed4 frag(v2f i) : SV_Target
            {
                float3 n = normalize(i.worldNormal);
                float3 v = normalize(i.viewDir);

                float grazing = 1.0 - saturate(abs(dot(n, v)));
                float density = _Density * (_Floor + grazing * _EdgeGain);

                return fixed4(density, density, density, 1);
            }
            ENDCG
        }
    }

    Fallback Off
}
