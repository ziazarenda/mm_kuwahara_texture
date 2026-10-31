VERSION 1.0.0 - NOW A REGULAR ADD-ON
Earlier versions installed as an extension. From 1.0.0 this is a regular add-on:
disable and remove the old extension first (Preferences > Get Extensions), then
install the ZIP with Preferences > Add-ons > Install from Disk and enable it.
The filter output is unchanged. The shaders now live in the shaders folder, and
running the filter from a script no longer fails.

MATERIAL MAKER KUWAHARA 0.5.0 - LOSSLESS PNG EXPORT

WHAT CHANGED
Adds Filtered image, PNG compression and Export PNG controls to the existing
Image Editor sidebar. The last generated image is selected automatically.
Compression defaults to 80 (high compression with reasonable save time).
Choose 100 for maximum compression or lower settings for faster saving.
The native PNG exporter writes encoded image bytes directly: no view transform,
render color management, image resizing or image-quality reduction is applied.
RGB and alpha are retained. sRGB color images receive an sRGB metadata tag;
Non-Color images do not. The generated image stays packed in the .blend.

USE
1. Install this ZIP via Preferences > Add-ons > Install from Disk.
2. Image Editor > N > MM Kuwahara.
3. Generate a filtered image as usual, or select an existing verified filtered
   image in the new Filtered image dropdown (no recalculation required).
4. Leave PNG compression at 80, or adjust it from 0 to 100.
5. Click Export PNG and choose the filename/location. The file dialog also
   offers a Compression setting; your selected value is remembered in the scene.
The compression applies to this add-on button. Blender's separate Image > Save As
has its own settings and is not modified by this add-on.

MEASURED ON THE USER'S VERIFIED 2048x2048 RGBA IMAGE
Received reference PNG: 3,580,077 bytes (3.41 MiB).
Custom PNG exporter, compression 80: 1,946,010 bytes (1.86 MiB), about 2.2 sec.
Custom PNG exporter, compression 100: 1,778,205 bytes (1.70 MiB), about 14.8 sec.
Times are from the authoring environment, not a prediction for the user's CPU.
Sizes depend on texture content. Compression cannot guarantee a fixed file size.
Decoded RGBA pixels match the verified input at every tested setting.
Encoded PNG file bytes/hashes will differ with compression and metadata choices.

IMPLEMENTATION / DEPENDENCIES
Uses standard Python zlib/struct and Blender's bundled NumPy. No Pillow, pip
installation or other external image dependency is required. PNG uses adaptive
per-row None/Sub/Up/Average/Paeth filtering and adjustable zlib compression.
Completed files are moved into place atomically to avoid partial output files.
This exporter currently writes 8-bit RGBA only, matching this add-on's output.
There is no lossy-quality slider; every compression setting is lossless.

VALIDATION
All shader strings remain byte-identical to verified version 0.4.3. Python syntax,
extension manifest and ZIP integrity checked. The PNG writer was decoded with an
independent Pillow reader: edge-size, random RGBA and transparent-colored-pixel
cases passed at compression 0, 50 and 100. The verified full wood texture passed
at compression 0, 50, 80, 90 and 100. Every RGB and alpha byte was preserved.
The actual export operator method was exercised with a simulated Blender byte
image: row inversion, normalized-byte recovery, sRGB metadata and transparency
were preserved. All 256 byte values round-trip through normalized float storage.
Blender itself is unavailable in the authoring environment, so the new file-dialog
and live UI flow still need a user run. Existing filtering is unchanged.

PREVIOUS EXACT-MATCH VALIDATION
The user's 0.4.3 captures matched all five Material Maker stages exactly for the
supplied wood texture/settings. The user's final 2048 PNG files also matched
byte-for-byte. This confirms that tested case, not every possible source or setting.

DIAGNOSTICS
Existing capture/export controls remain. NPZ arrays use logical top-first storage
as in 0.4.3, matching Material Maker EXRs directly without flipping tensor signs.

Supported input restrictions remain: single square 8-bit sRGB or Non-Color still
images, opaque filtered output; no UDIMs, movies, float source images or custom
color-space processing. See LICENSES.txt for upstream attribution and licensing.
