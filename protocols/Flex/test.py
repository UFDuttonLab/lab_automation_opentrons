'''
Vacuum manifold test protocol.

Runs two vacuum cycles to check out the Opentrons Vacuum Module (vacuum
manifold) before it's used in an actual extraction protocol:
  1. Empty module - confirms the module responds to commands and can pull
     to/hold the target pressure with nothing loaded on it.
  2. Collar + filter plate - confirms the manifold can pull and hold vacuum
     through a loaded collar and plate (a seal/leak check).

Note: Opentrons Flex checks the full deck setup before a run starts, so the
collar and filter plate used in test 2 need to already be on the manifold
before you hit start, even though the code only "attaches" them partway
through. Test 1 still runs first in the run log/timeline - just have the
collar + plate ready on the bench (or already seated) before you begin.
'''

from opentrons import protocol_api
from typing import cast
from opentrons.protocol_api import (
    ProtocolContext,
    VacuumModuleContext,
)

metadata = {
    'protocolName': 'Vacuum Manifold Test',
    'author': 'Tavis Goldwire',
    'description': 'Smoke test for the vacuum manifold: an empty-module cycle followed by a collar + filter plate seal check, at a set pressure and time.',
    'source': 'Tavis'
}


requirements = {
    'robotType': 'Flex',
    'apiLevel': '2.30'
}


def add_parameters(parameters):
    parameters.add_float(
        display_name="Pressure",
        variable_name="pressure",
        description="Vacuum pressure to pull, in mbar.",
        default=-400,
        minimum=-800.0,
        maximum=0.0
    )

    parameters.add_int(
        display_name="Time",
        variable_name="time",
        description="Duration to hold the vacuum, in seconds.",
        default=60,
        minimum=1,
        maximum=600
    )

    parameters.add_str(
        variable_name="collar",
        display_name="Vacuum Collar",
        description="The kind of Collar (Opentrons Tall or Opentrons Short)",
        default="opentrons_vacuum_manifold_collar_tall",
        choices=[
            {
                "display_name": "Opentrons Short",
                "value": "opentrons_vacuum_manifold_collar_short",
            },
            {
                "display_name": "Opentrons Tall",
                "value": "opentrons_vacuum_manifold_collar_tall",
            },
        ],
    )


def vacuum(ctx, vm_mod, pressure, time, vent=True, equalize_time=20):
        '''
        Function to operate the vacuum manifold. 
        pressure: vacuum pressure in mbars (from 0 to -800)
        time: duration of the vacuum in seconds
        vent: variable to indicate wether to open the vent (true) or not (false) after vacuuming
        equilize_time: time in seconds to wait after vacuuming to let the pressure equalize \
            before moving to the next step
        '''
        vm_mod.close_vent()
        task1 = vm_mod.start_set_vacuum_pressure(pressure, time, vent_after=vent, equalize_timeout_s=equalize_time)
        ctx.wait_for_tasks([task1])


def run(ctx: protocol_api.ProtocolContext):
    # LOAD MODULE

    vm_mod = cast(
            VacuumModuleContext,
            ctx.load_module(module_name="vacuumModuleV1", location="A3"))
    # Vacuum module loaded in deck position A3.

    #### TEST 1: EMPTY MODULE ##################

    ctx.comment(f"Test 1/2 - empty module: pulling {ctx.params.pressure} mbar for {ctx.params.time} s")
    vacuum(ctx, vm_mod, ctx.params.pressure, ctx.params.time)
    ctx.comment("Test 1 complete - module vented.")

    #### TEST 2: COLLAR + FILTER PLATE (SEAL CHECK) ##################

    manifold_collar = vm_mod.load_adapter(ctx.params.collar)
    # Manifold collar loaded on the vacuum manifold

    test_plate = manifold_collar.load_labware(
        "thermoscientificnunc_96_wellplate_1000ul_filter",
        "Vacuum Test Filter Plate")
    # Standing in for the Zymo-Spin I-96-Z Plate - any filter plate that seats
    # in the collar works for a leak/seal check. Swap for the real labware
    # definition once one exists for the ZymoBIOMICS kit.

    ctx.comment(f"Test 2/2 - collar + filter plate: pulling {ctx.params.pressure} mbar for {ctx.params.time} s")
    vacuum(ctx, vm_mod, ctx.params.pressure, ctx.params.time)
    ctx.comment("Test 2 complete - module vented.")
