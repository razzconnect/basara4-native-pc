"""The Vulkan check before the game starts (launcher/basara4_launcher.py probe_vulkan, vulkan_problem).

The game needs Vulkan 1.3 from the loader and from the GPU it renders on; below that it stops before
its first log line or Windows refuses to start it (exit 0xC0000139). vulkan-1.dll is replaced here by
a fake that answers through the same ctypes pointers the real loader writes to.
"""
import types
import unittest

from launcher_fixtures import bl

DGPU, IGPU, CPU = 2, 1, 4  # VkPhysicalDeviceType


def fake_vulkan(loader=(1, 3), instance=True, devices=(), fail_in=None):
    """A vulkan-1.dll stand-in: loader version (None: a 1.0 loader without vkEnumerateInstanceVersion),
    whether an instance can be made, and the GPUs as (name, deviceType, (major, minor))."""
    dll = types.SimpleNamespace(destroyed=False)
    if loader is not None:
        def enumerate_instance_version(out):
            out.contents.value = loader[0] << 22 | loader[1] << 12
            return 0
        dll.vkEnumerateInstanceVersion = enumerate_instance_version

    def create_instance(_info, _allocator, out):
        if not instance:
            return -9  # VK_ERROR_INCOMPATIBLE_DRIVER
        out.contents.value = 0x1000
        return 0

    def enumerate_devices(_instance, count, handles):
        if handles is None:
            count.contents.value = len(devices)
        else:
            for i in range(len(devices)):
                handles[i] = i + 1
        return 0

    def device_properties(handle, out):
        if fail_in == "properties":
            raise OSError("exception: access violation reading 0x0")
        name, kind, (major, minor) = devices[handle - 1]
        out.contents.deviceName = name.encode()
        out.contents.deviceType = kind
        out.contents.apiVersion = major << 22 | minor << 12

    def destroy_instance(_instance, _allocator):
        dll.destroyed = True

    dll.vkCreateInstance = create_instance
    dll.vkEnumeratePhysicalDevices = enumerate_devices
    dll.vkGetPhysicalDeviceProperties = device_properties
    dll.vkDestroyInstance = destroy_instance
    return dll


def probe(**kwargs):
    dll = fake_vulkan(**kwargs)
    return bl.probe_vulkan(load=lambda name: dll), dll


def missing(name):
    raise OSError(f"[WinError 126] could not find {name}")


def problem(result, gpu_index=-1):
    """vulkan_problem for the GPU the launcher would start the game on (the same rule as _env)."""
    return bl.vulkan_problem(result, bl.game_gpu_id(gpu_index, bl.gpu_choices(result)))


class ProbeTests(unittest.TestCase):
    def test_no_vulkan_dll(self):
        result = bl.probe_vulkan(load=missing)
        self.assertFalse(result.loaded)
        self.assertIn("vulkan-1.dll", result.error)
        self.assertEqual(problem(result), "missing")
        self.assertEqual(bl.gpu_choices(result), [(-1, "Auto (Prioritaskan GPU Diskrit)", True)])

    def test_vulkan_1_0_loader_is_old(self):
        # The user report: exit 0xC0000139, empty log, no GPU listed. A 1.0 loader has no
        # vkEnumerateInstanceVersion (nor the 1.1 functions basara4-core.exe imports).
        result, _ = probe(loader=None, instance=False)
        self.assertEqual((result.loaded, result.loader, result.devices, result.error), (True, (1, 0), [], ""))
        self.assertEqual(problem(result), "old")

    def test_loader_below_1_3_is_old_even_with_a_1_3_gpu(self):
        result, _ = probe(loader=(1, 2), devices=[("GPU", DGPU, (1, 3))])
        self.assertEqual(result.loader, (1, 2))
        self.assertEqual(problem(result), "old")

    def test_no_instance_or_no_gpu(self):
        result, _ = probe(instance=False)
        self.assertFalse(result.instance)
        self.assertEqual(problem(result), "no_gpu")
        result, dll = probe(devices=[])
        self.assertTrue(result.instance and dll.destroyed)
        self.assertEqual(problem(result), "no_gpu")

    def test_every_gpu_below_1_3_is_old(self):
        result, _ = probe(devices=[("Old iGPU", IGPU, (1, 2)), ("Old dGPU", DGPU, (1, 1))])
        for gpu_index in (-1, 0, 1, 7):
            self.assertEqual(problem(result, gpu_index), "old", gpu_index)

    def test_the_gpu_the_game_uses_decides(self):
        # Auto starts the game on the first discrete GPU (_env), even when only the iGPU has 1.3.
        result, _ = probe(devices=[("Old dGPU", DGPU, (1, 2)), ("New iGPU", IGPU, (1, 3))])
        self.assertEqual(problem(result, -1), "old")
        self.assertEqual(problem(result, 0), "old")
        self.assertIsNone(problem(result, 1))
        # A saved choice outside the list is the game's own choice, which takes the 1.3 GPU.
        self.assertIsNone(problem(result, 5))
        # No discrete GPU: Auto leaves the choice to the game.
        result, _ = probe(devices=[("Old iGPU", IGPU, (1, 2)), ("New iGPU", IGPU, (1, 3))])
        self.assertIsNone(bl.game_gpu_id(-1, bl.gpu_choices(result)))
        self.assertIsNone(problem(result, -1))
        self.assertEqual(problem(result, 0), "old")

    def test_newer_versions_pass(self):
        result, _ = probe(loader=(1, 4), devices=[("RTX", DGPU, (1, 4)), ("Radeon", IGPU, (1, 3))])
        for gpu_index in (-1, 0, 1):
            self.assertIsNone(problem(result, gpu_index), gpu_index)
        result, _ = probe(loader=(2, 0), devices=[("Future", DGPU, (2, 0))])
        self.assertIsNone(problem(result))

    def test_unknown_loader_version_is_judged_by_the_gpus(self):
        dll = fake_vulkan(devices=[("GPU", DGPU, (1, 3))])
        dll.vkEnumerateInstanceVersion = lambda out: -1  # the call failed: version unknown
        result = bl.probe_vulkan(load=lambda name: dll)
        self.assertIsNone(result.loader)
        self.assertIsNone(problem(result))

    def test_a_probe_that_stops_part_way_keeps_what_it_read_and_warns_nobody(self):
        result, _ = probe(devices=[("GPU", DGPU, (1, 2))], fail_in="properties")
        self.assertTrue(result.loaded)
        self.assertEqual(result.loader, (1, 3))
        self.assertIn("access violation", result.error)
        self.assertIsNone(problem(result))  # nothing is certain: no dialog
        self.assertIn("probe stopped: OSError", bl.describe_vulkan(result))

    def test_gpu_list_keeps_its_labels_order_and_indices(self):
        result, _ = probe(devices=[("NVIDIA GeForce RTX 4070", DGPU, (1, 4)),
                                   ("AMD Radeon(TM) Graphics", IGPU, (1, 4)), ("llvmpipe", CPU, (1, 3))])
        self.assertEqual(bl.gpu_choices(result), [
            (-1, "Auto (Prioritaskan GPU Diskrit)", True),
            (0, "GPU 0: NVIDIA GeForce RTX 4070 (dGPU)", True),
            (1, "GPU 1: AMD Radeon(TM) Graphics (iGPU)", False),
            (2, "GPU 2: llvmpipe (GPU)", False)])
        self.assertEqual(bl.game_gpu_id(-1, bl.gpu_choices(result)), 0)
        self.assertEqual(bl.game_gpu_id(2, bl.gpu_choices(result)), 2)

    def test_the_launcher_list_comes_from_its_probe(self):
        self.assertEqual(bl.SYSTEM_GPUS, bl.gpu_choices(bl.VULKAN))
        self.assertEqual(bl.VULKAN_REQUIRED, (1, 3))  # TargetVulkanApiVersion in vk_platform.h


class ReportTests(unittest.TestCase):
    def test_describe_vulkan(self):
        self.assertEqual(bl.describe_vulkan(bl.probe_vulkan(load=missing)),
                         "not found (vulkan-1.dll: [WinError 126] could not find vulkan-1.dll); needs 1.3")
        self.assertEqual(bl.describe_vulkan(probe(loader=None, instance=False)[0]),
                         "loader 1.0, no instance; needs 1.3")
        self.assertEqual(bl.describe_vulkan(probe(devices=[])[0]), "loader 1.3, no GPU; needs 1.3")
        self.assertEqual(bl.describe_vulkan(probe(loader=(1, 4), devices=[("A", DGPU, (1, 4)),
                                                                          ("B", IGPU, (1, 2))])[0]),
                         "loader 1.4, GPU 0 1.4, GPU 1 1.2; needs 1.3")


class WarningTextTests(unittest.TestCase):
    def test_wording(self):
        self.assertEqual(bl.vulkan_warning("no_gpu"), (
            "Launcher tidak menemukan kartu grafis yang mendukung Vulkan di PC ini. Game ini membutuhkan GPU "
            "dan driver dengan dukungan Vulkan.\n\n"
            "Yang bisa dicoba:\n"
            "1. Perbarui driver kartu grafis dari situs NVIDIA, AMD, atau Intel, atau lewat Windows Update. "
            "Lalu buka launcher lagi.\n"
            "2. Kalau PC punya kartu grafis terpisah, pastikan drivernya terpasang, bukan hanya driver bawaan "
            "Windows.\n"
            "3. Beberapa kartu grafis lama memang tidak mendukung Vulkan. Kalau sudah diperbarui dan pesan ini "
            "tetap muncul, kemungkinan PC ini belum bisa menjalankan game.\n\n"
            "Tetap jalankan game?"))
        self.assertTrue(bl.vulkan_warning("missing").startswith(
            "File Vulkan bawaan Windows (vulkan-1.dll) tidak ditemukan di PC ini. Game ini membutuhkan"))
        self.assertTrue(bl.vulkan_warning("old").startswith(
            "Komponen Vulkan di PC ini terlalu lama untuk game ini. Game ini membutuhkan"))

    def test_every_problem_has_a_message(self):
        found = {problem(bl.probe_vulkan(load=missing)), problem(probe(instance=False)[0]),
                 problem(probe(loader=(1, 2))[0])}
        self.assertEqual(found, set(bl.VULKAN_WARNING_OPENING))
        tails = {bl.vulkan_warning(p).split(" Game ini", 1)[1] for p in found}
        self.assertEqual(len(tails), 1)  # only the opening sentence differs


if __name__ == "__main__":
    unittest.main()
