import bpy
import json
from mathutils import Vector

SPEC = json.loads('[{"name": "Bench_01_torus", "primitive": "torus", "location": [0.0, 0.0, 1.0], "material": "BenchMat_01", "color": [0.85, 0.12, 0.08, 1.0]}, {"name": "Bench_02_cube", "primitive": "cube", "location": [0.0, 3.5, 1.0], "material": "BenchMat_02", "color": [0.08, 0.36, 0.9, 1.0]}, {"name": "Bench_03_cylinder", "primitive": "cylinder", "location": [3.0, 2.0, 1.0], "material": "BenchMat_03", "color": [0.12, 0.72, 0.28, 1.0]}, {"name": "Bench_04_cone", "primitive": "cone", "location": [-3.0, 2.0, 1.0], "material": "BenchMat_04", "color": [0.92, 0.48, 0.06, 1.0]}, {"name": "Bench_05_uv_sphere", "primitive": "uv_sphere", "location": [3.0, -2.0, 1.0], "material": "BenchMat_05", "color": [0.62, 0.16, 0.82, 1.0]}]')
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

def material(name, color):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    return mat

for item in SPEC:
    getattr(bpy.ops.mesh, {
        "cube": "primitive_cube_add",
        "uv_sphere": "primitive_uv_sphere_add",
        "cylinder": "primitive_cylinder_add",
        "cone": "primitive_cone_add",
        "torus": "primitive_torus_add",
    }[item["primitive"]])(location=item["location"])
    obj = bpy.context.active_object
    obj.name = item["name"]
    obj["infinity_benchmark_id"] = 'random-scene-260802'
    obj["infinity_primitive"] = item["primitive"]
    obj.data.materials.append(material(item["material"], item["color"]))

bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
ground = bpy.context.active_object
ground.name = "Bench_Ground"
ground.data.materials.append(material("BenchGround", (0.06, 0.07, 0.09, 1.0)))

bpy.ops.object.light_add(type="AREA", location=(2, -4, 7))
light = bpy.context.active_object
light.name = "Bench_KeyLight"
light.data.energy = 1200
light.data.shape = "DISK"
light.data.size = 5

bpy.ops.object.camera_add(location=(10, -12, 9))
camera = bpy.context.active_object
camera.name = "Bench_Camera"
camera.rotation_euler = ((Vector((0, 0, 1)) - camera.location).to_track_quat("-Z", "Y").to_euler())
bpy.context.scene.camera = camera

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.render.resolution_x = 640
scene.render.resolution_y = 480
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.world.color = (0.025, 0.03, 0.05)
bpy.ops.wm.save_as_mainfile(filepath='C:\\Users\\caleb\\infinity-code\\backend\\data\\eval_reports\\blender_random_scene_20260801_225039\\random_scene.blend')
