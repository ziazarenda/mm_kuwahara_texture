// The MIT License - Copyright (c) 2022 Garrett Gunnell
// Adapted from https://github.com/GarrettGunnell/Post-Processing/
// blob/871a7ffcc44174f237b4c0127db51026c438d7a4/Assets/Kuwahara%20Filter/AnisotropicKuwahara.shader

vec4 calc_tensor(vec2 uv) {
    vec2 d = 1.0/vec2(resolution);
    vec3 Sx = (
        1.0 * sample_color(uv + vec2(-d.x, -d.y)).rgb +
        2.0 * sample_color(uv + vec2(-d.x,  0.0)).rgb +
        1.0 * sample_color(uv + vec2(-d.x,  d.y)).rgb +
        -1.0 * sample_color(uv + vec2(d.x, -d.y)).rgb +
        -2.0 * sample_color(uv + vec2(d.x,  0.0)).rgb +
        -1.0 * sample_color(uv + vec2(d.x,  d.y)).rgb
    ) * 0.25;

    vec3 Sy = (
        1.0 * sample_color(uv + vec2(-d.x, -d.y)).rgb +
        2.0 * sample_color(uv + vec2( 0.0, -d.y)).rgb +
        1.0 * sample_color(uv + vec2( d.x, -d.y)).rgb +
        -1.0 * sample_color(uv + vec2(-d.x, d.y)).rgb +
        -2.0 * sample_color(uv + vec2( 0.0, d.y)).rgb +
        -1.0 * sample_color(uv + vec2( d.x, d.y)).rgb
    ) * 0.25;

    return vec4(dot(Sx, Sx), dot(Sy, Sy), dot(Sx, Sy), 1.0);
}
