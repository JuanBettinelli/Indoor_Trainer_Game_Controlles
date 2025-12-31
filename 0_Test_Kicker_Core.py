import asyncio
from bleak import BleakClient

from pycycling.fitness_machine_service import FitnessMachineService


async def run(address):
    async with BleakClient(address) as client:
        def my_page_handler(data):
            print(data)

        trainer = FitnessMachineService(client)
        trainer.set_indoor_bike_data_handler(my_page_handler)
        await trainer.enable_indoor_bike_data_notify()
        await trainer.set_target_resistance_level(20)
        await asyncio.sleep(20.0)
        await trainer.set_target_resistance_level(40)
        await asyncio.sleep(20.0)



if __name__ == "__main__":
    import os

    os.environ["PYTHONASYNCIODEBUG"] = str(1)

    device_address = "D181282F-9CD3-AF69-9E8B-1A8113A614E6"
    asyncio.run(run(device_address))


    # KICKR CORE 5836: D181282F-9CD3-AF69-9E8B-1A8113A614E6