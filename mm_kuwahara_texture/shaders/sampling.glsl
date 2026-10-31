
ivec2 wrap_pixel(ivec2 p, ivec2 n) { return ((p % n) + n) % n; }
vec4 read_nearest(sampler2D tex, vec2 uv) {
    ivec2 n = textureSize(tex, 0);
    return texelFetch(tex, wrap_pixel(ivec2(floor(fract(uv)*vec2(n))), n), 0);
}
vec4 read_linear(sampler2D tex, vec2 uv) {
    ivec2 n = textureSize(tex, 0);
    vec2 p = fract(uv)*vec2(n)-vec2(0.5);
    ivec2 q = ivec2(floor(p)); vec2 t = fract(p);
    vec4 a = texelFetch(tex, wrap_pixel(q,n),0);
    vec4 b = texelFetch(tex, wrap_pixel(q+ivec2(1,0),n),0);
    vec4 c = texelFetch(tex, wrap_pixel(q+ivec2(0,1),n),0);
    vec4 d = texelFetch(tex, wrap_pixel(q+ivec2(1,1),n),0);
    vec4 value = mix(mix(a,b,t.x),mix(c,d,t.x),t.y);
    // Match the sampled-color precision measured in the Material Maker probes:
    // positive normal half values, round-to-nearest with halfway values upward.
    // Supported byte inputs and clamped filter colors lie in [0,1]; nonzero
    // source samples here are above the half-subnormal range.
    return uintBitsToFloat((floatBitsToUint(value) + uvec4(0x1000u))
    & uvec4(0xFFFFE000u));
}
vec4 sample_color(vec2 uv) { return read_linear(color_tex, uv); }
vec4 sample_tensor(vec2 uv) { return read_nearest(tensor_tex, uv); }
