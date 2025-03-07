# pylint: disable=too-many-lines
"""Tests for distribution algorithm.

Copyright
Copyright © 2022 Frequenz Energy-as-a-Service GmbH

License
MIT
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import frequenz.api.microgrid.microgrid_pb2 as microgrid_pb
import pytest
from frequenz.api.microgrid.battery_pb2 import Battery
from frequenz.api.microgrid.battery_pb2 import Data as PbBatteryData
from frequenz.api.microgrid.battery_pb2 import Properties as BatteryProperties
from frequenz.api.microgrid.common_pb2 import AC, DC, Bounds
from frequenz.api.microgrid.common_pb2 import Metric as PbMetric
from frequenz.api.microgrid.common_pb2 import MetricAggregation
from frequenz.api.microgrid.inverter_pb2 import Data as PbInverterData
from frequenz.api.microgrid.inverter_pb2 import Inverter
from google.protobuf.timestamp_pb2 import Timestamp  # pylint: disable=no-name-in-module

from frequenz.sdk.microgrid.component_data import BatteryData, InverterData
from frequenz.sdk.power_distribution.distribution_algorithm import DistributionAlgorithm
from frequenz.sdk.power_distribution.utils import InvBatPair


@dataclass
class Bound:
    """Class to create protobuf Bound"""

    lower: float
    upper: float

    def to_protobuf(self) -> Bounds:
        """Create protobuf Bounds message from that instance.

        Returns:
            Protobuf Bounds message.
        """
        return Bounds(lower=self.lower, upper=self.upper)


@dataclass
class Metric:
    """Class to create protobuf Metric"""

    now: Optional[float]
    bound: Optional[Bound] = None

    def to_protobuf(self) -> PbMetric:
        """Create protobuf Metric message from that instance.

        Returns:
           Protobuf Metric
        """
        if self.now is None:
            if self.bound is None:
                return PbMetric()
            return PbMetric(system_bounds=self.bound.to_protobuf())
        if self.bound is None:
            return PbMetric(value=self.now)

        return PbMetric(value=self.now, system_bounds=self.bound.to_protobuf())


def create_battery_msg(  # pylint: disable=too-many-arguments
    component_id: int,
    capacity: Metric,
    soc: Metric,
    power: Bound,
    timestamp: datetime = datetime.utcnow(),
) -> microgrid_pb.ComponentData:
    """Create protobuf battery components with given arguments.

    Args:
        component_id: id of that component
        capacity: capacity
        soc: soc
        power_supply: supply bound
        power_consumption: consumption bound
        timestamp: timestamp of the message

    Returns:
        Protobuf battery component with data above
    """
    pb_timestamp = Timestamp()
    pb_timestamp.FromDatetime(timestamp)
    capacitypb = capacity.to_protobuf()
    socpb = soc.to_protobuf()
    return microgrid_pb.ComponentData(
        id=component_id,
        ts=pb_timestamp,
        battery=Battery(
            properties=BatteryProperties(capacity=capacitypb.value),
            data=PbBatteryData(
                soc=MetricAggregation(
                    avg=socpb.value, system_bounds=socpb.system_bounds
                ),
                dc=DC(
                    power=PbMetric(system_bounds=power.to_protobuf()),
                ),
            ),
        ),
    )


def create_inverter_msg(
    component_id: int,
    power: Bound,
    timestamp: datetime = datetime.utcnow(),
) -> microgrid_pb.ComponentData:
    """Create protobuf inverter components with given arguments.

    Args:
        component_id: id of that component
        power_supply: Supply bound
        power_consumption: Consumption bound inverter
        timestamp: Timestamp from the message

    Returns:
        Protobuf inverter component with data above.
    """
    pb_timestamp = Timestamp()
    pb_timestamp.FromDatetime(timestamp)
    return microgrid_pb.ComponentData(
        id=component_id,
        ts=pb_timestamp,
        inverter=Inverter(
            data=PbInverterData(
                ac=AC(power_active=PbMetric(system_bounds=power.to_protobuf())),
            )
        ),
    )


class TestDistributionAlgorithm:  # pylint: disable=too-many-public-methods
    """Test whether the algorithm works as expected."""

    # pylint: disable=protected-access

    def create_components_with_capacity(
        self, num: int, capacity: List[float]
    ) -> List[InvBatPair]:
        """Create components with given capacity."""

        components: List[InvBatPair] = []
        for i in range(0, num):
            bat_msg = microgrid_pb.ComponentData(
                id=2 * i,
                battery=Battery(properties=BatteryProperties(capacity=capacity[i])),
            )
            battery = BatteryData(bat_msg)

            inv_msg = microgrid_pb.ComponentData(id=2 * i + 1, inverter=Inverter())
            inverter = InverterData(inv_msg)

            components.append(InvBatPair(battery, inverter))
        return components

    def test_total_capacity_all_0(self) -> None:
        """Raise error if all batteries have no capacity."""
        capacity = [0.0] * 4
        components = self.create_components_with_capacity(4, capacity)
        algorithm = DistributionAlgorithm(distributor_exponent=1)
        with pytest.raises(ValueError):
            algorithm._total_capacity(components)  # pylint: disable=protected-access

    def test_total_capacity(self) -> None:
        """Test if capacity is computed properly."""
        capacity: List[float] = list(range(4))
        components = self.create_components_with_capacity(4, capacity)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm._total_capacity(components)
        assert result == sum(list(range(4)))

    def test_distribute_power_one_battery(self) -> None:
        """Distribute power between one battery."""
        capacity: List[float] = [98000]
        components = self.create_components_with_capacity(1, capacity)

        available_soc: Dict[int, float] = {0: 40}
        upper_bounds = {1: 500}

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm._distribute_power(  # pylint: disable=protected-access
            components, 650, available_soc, upper_bounds
        )

        assert result.distribution == {1: 500}
        assert result.remaining_power == 150

    def test_distribute_power_two_batteries_1(self) -> None:
        """Test when the batteries has different SoC.

        First battery has two times more SoC to use, so first battery should have more
        power assigned.
        """
        capacity: List[float] = [98000, 98000]
        components = self.create_components_with_capacity(2, capacity)

        available_soc: Dict[int, float] = {0: 40, 2: 20}
        upper_bounds = {1: 500, 3: 500}

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm._distribute_power(  # pylint: disable=protected-access
            components, 600, available_soc, upper_bounds
        )

        assert result.distribution == {1: 400, 3: 200}
        assert result.remaining_power == 0

    def test_distribute_power_two_batteries_2(self) -> None:
        """Test when the batteries has different SoC.

        First battery has two times less capacity to use, so first
        battery should be have two times less power.
        """
        capacity: List[float] = [49000, 98000]
        components = self.create_components_with_capacity(2, capacity)

        available_soc: Dict[int, float] = {0: 20, 2: 20}
        upper_bounds = {1: 500, 3: 500}

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm._distribute_power(  # pylint: disable=protected-access
            components, 600, available_soc, upper_bounds
        )

        # Ceil/floor result.
        assert result.distribution == {1: 199, 3: 399}
        assert result.remaining_power == 2

    def test_distribute_power_two_batteries_bounds(self) -> None:
        """Test two batteries.

        First battery has two times less capacity, but
        two times more SoC. So the distributed power should be equal
        for each battery.
        """
        capacity: List[float] = [49000, 98000]
        components = self.create_components_with_capacity(2, capacity)

        available_soc: Dict[int, float] = {0: 40, 2: 20}
        upper_bounds = {1: 250, 3: 330}

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm._distribute_power(  # pylint: disable=protected-access
            components, 600, available_soc, upper_bounds
        )

        assert result.distribution == {1: 250, 3: 330}
        assert result.remaining_power == 20

    def test_distribute_power_three_batteries(self) -> None:
        """Test whether the distribution works ok for more batteries."""
        capacity: List[float] = [49000, 98000, 49000]
        components = self.create_components_with_capacity(3, capacity)

        available_soc: Dict[int, float] = {0: 40, 2: 20, 4: 20}
        upper_bounds = {1: 1000, 3: 3400, 5: 3550}

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm._distribute_power(  # pylint: disable=protected-access
            components, 1000, available_soc, upper_bounds
        )

        assert result.distribution == {1: 400, 3: 400, 5: 200}
        assert result.remaining_power == 0

    def test_distribute_power_three_batteries_2(self) -> None:
        """Test whether the power which couldn't be distributed is correct."""
        capacity: List[float] = [98000, 49000, 49000]
        components = self.create_components_with_capacity(3, capacity)

        available_soc: Dict[int, float] = {0: 80, 2: 10, 4: 20}
        upper_bounds = {1: 400, 3: 3400, 5: 300}

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm._distribute_power(  # pylint: disable=protected-access
            components, 1000, available_soc, upper_bounds
        )

        assert result.distribution == {1: 400, 3: 300, 5: 300}
        assert result.remaining_power == 0

    def test_distribute_power_three_batteries_3(self) -> None:
        """Test with batteries with no capacity"""
        capacity: List[float] = [0, 49000, 0]
        components = self.create_components_with_capacity(3, capacity)

        available_soc: Dict[int, float] = {0: 80, 2: 10, 4: 20}
        upper_bounds = {1: 500, 3: 300, 5: 300}

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm._distribute_power(  # pylint: disable=protected-access
            components, 1000, available_soc, upper_bounds
        )

        assert result.distribution == {1: 0, 3: 300, 5: 0}
        assert result.remaining_power == 700

    def create_components(  # pylint: disable=too-many-arguments
        self,
        num: int,
        capacity: List[Metric],
        soc: List[Metric],
        power: List[Bound],
    ) -> List[InvBatPair]:
        """Create components with given arguments.

        Args:
            num: Number of components
            capacity: Capacity for each battery
            soc: SoC for each battery
            soc_bounds: SoC bounds for each battery
            supply_bounds: Supply bounds for each battery and inverter
            consumption_bounds: Consumption bounds for each battery and inverter

        Returns:
            List of the components
        """

        components: List[InvBatPair] = []
        for i in range(0, num):
            battery = BatteryData(
                create_battery_msg(
                    2 * i,
                    capacity[i],
                    soc[i],
                    power[2 * i],
                )
            )
            inverter = InverterData(create_inverter_msg(2 * i + 1, power[2 * i + 1]))
            components.append(InvBatPair(battery, inverter))
        return components

    # Test distribute supply power
    def test_supply_three_batteries_1(self) -> None:
        """Test distribute supply power for batteries with different SoC."""
        capacity: List[Metric] = [Metric(49000), Metric(49000), Metric(49000)]

        soc: List[Metric] = [
            Metric(20.0, Bound(0, 60)),
            Metric(60.0, Bound(20, 80)),
            Metric(80.0, Bound(20, 80)),
        ]

        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(-900, 0),
            Bound(-1000, 0),
            Bound(-800, 0),
            Bound(-700, 0),
            Bound(-900, 0),
            Bound(-900, 0),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-1200, components)

        assert result.distribution == {1: -199, 3: -399, 5: -602}
        assert result.remaining_power == 0

    def test_supply_three_batteries_2(self) -> None:
        """Test distribute supply power."""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(49000)]
        soc: List[Metric] = [
            Metric(20.0, Bound(0, 50)),
            Metric(60.0, Bound(20, 80)),
            Metric(80.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(-900, 0),
            Bound(-1000, 0),
            Bound(-800, 0),
            Bound(-700, 0),
            Bound(-900, 0),
            Bound(-900, 0),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-1400, components)

        assert result.distribution == {1: -400, 3: -400, 5: -600}
        assert result.remaining_power == 0

    def test_supply_three_batteries_3(self) -> None:
        """Distribute supply power with small upper bounds."""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(49000)]
        soc: List[Metric] = [
            Metric(20.0, Bound(0, 50)),
            Metric(60.0, Bound(20, 80)),
            Metric(80.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        supply_bounds = [
            Bound(-600, 0),
            Bound(-1000, 0),
            Bound(-600, 0),
            Bound(-100, 0),
            Bound(-800, 0),
            Bound(-900, 0),
        ]
        components = self.create_components(3, capacity, soc, supply_bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-1400, components)

        assert result.distribution == {1: -500, 3: -100, 5: -800}
        assert result.remaining_power == 0

    def test_supply_three_batteries_4(self) -> None:
        """Distribute supply power with small upper bounds."""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(49000)]
        soc: List[Metric] = [
            Metric(20.0, Bound(0, 50)),
            Metric(60.0, Bound(20, 80)),
            Metric(80.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(-600, 0),
            Bound(-1000, 0),
            Bound(-600, 0),
            Bound(-100, 0),
            Bound(-800, 0),
            Bound(-900, 0),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-1700, components)

        assert result.distribution == {1: -600, 3: -100, 5: -800}
        assert result.remaining_power == -200

    def test_supply_three_batteries_5(self) -> None:
        """Test no capacity."""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(0.0)]
        soc: List[Metric] = [
            Metric(20.0, Bound(40, 90)),
            Metric(60.0, Bound(20, 80)),
            Metric(80.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        supply_bounds = [
            Bound(-600, 0),
            Bound(-1000, 0),
            Bound(-600, 0),
            Bound(-100, 0),
            Bound(-800, 0),
            Bound(-900, 0),
        ]
        components = self.create_components(3, capacity, soc, supply_bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-1700, components)

        assert result.distribution == {1: 0, 3: -100, 5: 0}
        assert result.remaining_power == -1600

    def test_supply_two_batteries_1(self) -> None:
        """Distribute supply power between two batteries."""
        capacity: List[Metric] = [Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(25.0, Bound(0, 80)),
            Metric(25.0, Bound(20, 80)),
        ]

        # consume bounds == 0 makes sure they are not used in supply algorithm
        supply_bounds = [
            Bound(-600, 0),
            Bound(-1000, 0),
            Bound(-600, 0),
            Bound(-1000, 0),
        ]
        components = self.create_components(2, capacity, soc, supply_bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-600, components)

        assert result.distribution == {1: -500, 3: -100}
        assert result.remaining_power == 0

    def test_supply_two_batteries_2(self) -> None:
        """Distribute supply power between two batteries."""
        capacity: List[Metric] = [Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(75.0, Bound(0, 80)),
            Metric(75.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        supply_bounds = [
            Bound(-600, 0),
            Bound(-1000, 0),
            Bound(-600, 0),
            Bound(-1000, 0),
        ]
        components = self.create_components(2, capacity, soc, supply_bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-600, components)

        assert result.distribution == {1: -347, 3: -253}
        assert result.remaining_power == 0

    # Test consumption power distribution
    def test_consumption_three_batteries_1(self) -> None:
        """Distribute consume power."""
        capacity: List[Metric] = [Metric(49000), Metric(49000), Metric(49000)]
        soc: List[Metric] = [
            Metric(80.0, Bound(0, 100)),
            Metric(40.0, Bound(20, 80)),
            Metric(20.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 900),
            Bound(0, 1000),
            Bound(0, 800),
            Bound(0, 700),
            Bound(0, 900),
            Bound(0, 900),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(1200, components)

        assert result.distribution == {1: 199, 3: 399, 5: 602}
        assert result.remaining_power == 0

    def test_consumption_three_batteries_2(self) -> None:
        """Distribute consume power."""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(49000)]
        soc: List[Metric] = [
            Metric(80.0, Bound(0, 100)),
            Metric(40.0, Bound(20, 80)),
            Metric(20.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 900),
            Bound(0, 1000),
            Bound(0, 800),
            Bound(0, 700),
            Bound(0, 900),
            Bound(0, 900),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(1400, components)

        assert result.distribution == {1: 400, 3: 400, 5: 600}
        assert result.remaining_power == 0

    def test_consumption_three_batteries_3(self) -> None:
        """Distribute consume power with small bounds."""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(49000)]
        soc: List[Metric] = [
            Metric(80.0, Bound(0, 100)),
            Metric(40.0, Bound(20, 80)),
            Metric(20.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 600),
            Bound(0, 1000),
            Bound(0, 600),
            Bound(0, 100),
            Bound(0, 800),
            Bound(0, 900),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(1400, components)

        assert result.distribution == {1: 500, 3: 100, 5: 800}
        assert result.remaining_power == 0

    def test_consumption_three_batteries_4(self) -> None:
        """Distribute consume power with small upper bounds."""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(49000)]
        soc: List[Metric] = [
            Metric(80.0, Bound(0, 100)),
            Metric(40.0, Bound(20, 80)),
            Metric(20.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 600),
            Bound(0, 1000),
            Bound(0, 600),
            Bound(0, 100),
            Bound(0, 800),
            Bound(0, 900),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(1700, components)

        assert result.distribution == {1: 600, 3: 100, 5: 800}
        assert result.remaining_power == 200

    def test_consumption_three_batteries_5(self) -> None:
        """Test what if some batteries has invalid SoC and capacity"""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(0.0)]
        soc: List[Metric] = [
            Metric(80.0, Bound(0, 50)),
            Metric(40.0, Bound(20, 80)),
            Metric(20.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 600),
            Bound(0, 1000),
            Bound(0, 600),
            Bound(0, 100),
            Bound(0, 800),
            Bound(0, 900),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(1700, components)

        assert result.distribution == {1: 0, 3: 100, 5: 0}
        assert result.remaining_power == 1600

    def test_consumption_three_batteries_6(self) -> None:
        """Distribute consume power."""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(49000)]
        soc: List[Metric] = [
            Metric(50.0, Bound(0, 50)),
            Metric(40.0, Bound(20, 80)),
            Metric(20.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 600),
            Bound(0, 1000),
            Bound(0, 600),
            Bound(0, 100),
            Bound(0, 800),
            Bound(0, 900),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(1700, components)

        assert result.distribution == {1: 0, 3: 100, 5: 800}
        assert result.remaining_power == 800

    def test_consumption_three_batteries_7(self) -> None:
        """Distribute consume power."""
        capacity: List[Metric] = [Metric(98000), Metric(49000), Metric(49000)]
        soc: List[Metric] = [
            Metric(20.0, Bound(0, 80)),
            Metric(79.6, Bound(20, 80)),
            Metric(80.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 500),
            Bound(0, 1000),
            Bound(0, 600),
            Bound(0, 700),
            Bound(0, 800),
            Bound(0, 900),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(500, components)

        assert result.distribution == {1: 499, 3: 1, 5: 0}
        assert result.remaining_power == 0

    def test_consumption_two_batteries_1(self) -> None:
        """Distribute consume power."""
        capacity: List[Metric] = [Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(75.0, Bound(20, 80)),
            Metric(75.0, Bound(0, 100)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 600),
            Bound(0, 1000),
            Bound(0, 600),
            Bound(0, 1000),
        ]
        components = self.create_components(2, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(600, components)

        assert result.distribution == {1: 100, 3: 500}
        assert result.remaining_power == 0

    def test_consumption_two_batteries_distribution_exponent(self) -> None:
        """Distribute consume power."""
        capacity: List[Metric] = [Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(70.0, Bound(20, 80)),
            Metric(50.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 9000),
            Bound(0, 9000),
            Bound(0, 9000),
            Bound(0, 9000),
        ]
        components = self.create_components(2, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(8000, components)

        assert result.distribution == {1: 2000, 3: 6000}
        assert result.remaining_power == 0

        algorithm2 = DistributionAlgorithm(distributor_exponent=2)
        result2 = algorithm2.distribute_power(8000, components)

        assert result2.distribution == {1: 800, 3: 7200}
        assert result2.remaining_power == 0

        algorithm3 = DistributionAlgorithm(distributor_exponent=3)
        result3 = algorithm3.distribute_power(8000, components)

        assert result3.distribution == {1: 285, 3: 7715}
        assert result3.remaining_power == 0

    def test_consumption_two_batteries_distribution_exponent_1(self) -> None:
        """Distribute consume power."""
        capacity: List[Metric] = [Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(50.0, Bound(20, 80)),
            Metric(20.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 9000),
            Bound(0, 9000),
            Bound(0, 9000),
            Bound(0, 9000),
        ]
        components = self.create_components(2, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(900, components)

        assert result.distribution == {1: 300, 3: 600}
        assert result.remaining_power == 0

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(8000, components)

        assert result.distribution == {1: 2666, 3: 5334}
        assert result.remaining_power == 0

        algorithm2 = DistributionAlgorithm(distributor_exponent=2)
        result2 = algorithm2.distribute_power(900, components)

        assert result2.distribution == {1: 180, 3: 720}
        assert result2.remaining_power == 0

        algorithm2 = DistributionAlgorithm(distributor_exponent=2)
        result2 = algorithm2.distribute_power(8000, components)

        assert result2.distribution == {1: 1600, 3: 6400}
        assert result2.remaining_power == 0

        algorithm2 = DistributionAlgorithm(distributor_exponent=3)
        result2 = algorithm2.distribute_power(900, components)

        assert result2.distribution == {1: 100, 3: 800}
        assert result2.remaining_power == 0

        algorithm3 = DistributionAlgorithm(distributor_exponent=3)
        result3 = algorithm3.distribute_power(8000, components)

        assert result3.distribution == {1: 888, 3: 7112}
        assert result3.remaining_power == 0

    def test_supply_two_batteries_distribution_exponent(self) -> None:
        """Distribute power."""
        capacity: List[Metric] = [Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(30.0, Bound(20, 80)),
            Metric(50.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
        ]
        components = self.create_components(2, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-8000, components)

        assert result.distribution == {1: -2000, 3: -6000}
        assert result.remaining_power == 0

        algorithm2 = DistributionAlgorithm(distributor_exponent=2)
        result2 = algorithm2.distribute_power(-8000, components)

        assert result2.distribution == {1: -800, 3: -7200}
        assert result2.remaining_power == 0

        algorithm3 = DistributionAlgorithm(distributor_exponent=3)
        result3 = algorithm3.distribute_power(-8000, components)

        assert result3.distribution == {1: -285, 3: -7715}
        assert result3.remaining_power == 0

    def test_supply_two_batteries_distribution_exponent_1(self) -> None:
        """Distribute power."""
        capacity: List[Metric] = [Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(50.0, Bound(20, 80)),
            Metric(80.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        supply_bounds = [
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
        ]
        components = self.create_components(2, capacity, soc, supply_bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-8000, components)

        assert result.distribution == {1: -2666, 3: -5334}
        assert result.remaining_power == 0

        algorithm2 = DistributionAlgorithm(distributor_exponent=2)
        result2 = algorithm2.distribute_power(-8000, components)

        assert result2.distribution == {1: -1600, 3: -6400}
        assert result2.remaining_power == 0

        algorithm3 = DistributionAlgorithm(distributor_exponent=3)
        result3 = algorithm3.distribute_power(-8000, components)

        assert result3.distribution == {1: -888, 3: -7112}
        assert result3.remaining_power == 0

    def test_supply_three_batteries_distribution_exponent_2(self) -> None:
        """Distribute power."""
        capacity: List[Metric] = [Metric(98000), Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(50.0, Bound(20, 80)),
            Metric(65.0, Bound(20, 80)),
            Metric(80.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
        ]
        components = self.create_components(3, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=1)
        result = algorithm.distribute_power(-8000, components)

        assert result.distribution == {1: -1777, 3: -2666, 5: -3557}
        assert result.remaining_power == 0

        algorithm2 = DistributionAlgorithm(distributor_exponent=2)
        result2 = algorithm2.distribute_power(-8000, components)

        assert result2.distribution == {1: -1103, 3: -2482, 5: -4415}
        assert result2.remaining_power == 0

        algorithm3 = DistributionAlgorithm(distributor_exponent=3)
        result3 = algorithm3.distribute_power(-8000, components)

        assert result3.distribution == {1: -646, 3: -2181, 5: -5173}
        assert result3.remaining_power == 0

    def test_supply_three_batteries_distribution_exponent_3(self) -> None:
        """Distribute power."""
        capacity: List[Metric] = [Metric(98000), Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(56.0, Bound(20, 80)),  # available SoC 36
            Metric(36.0, Bound(20, 80)),  # available SoC 16
            Metric(29.0, Bound(20, 80)),  # available SoC 9
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        supply_bounds = [
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
            Bound(-9000, 0),
        ]
        components = self.create_components(3, capacity, soc, supply_bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=0.5)
        result = algorithm.distribute_power(-1300, components)

        assert result.distribution == {1: -600, 3: -400, 5: -300}
        assert result.remaining_power == 0

        algorithm = DistributionAlgorithm(distributor_exponent=0)
        result = algorithm.distribute_power(-1200, components)

        assert result.distribution == {1: -400, 3: -400, 5: -400}
        assert result.remaining_power == 0

    def test_supply_two_batteries_distribution_exponent_less_then_1(self) -> None:
        """Distribute power."""
        capacity: List[Metric] = [Metric(98000), Metric(98000)]
        soc: List[Metric] = [
            Metric(44.0, Bound(20, 80)),
            Metric(64.0, Bound(20, 80)),
        ]
        # consume bounds == 0 makes sure they are not used in supply algorithm
        bounds = [
            Bound(0, 9000),
            Bound(0, 9000),
            Bound(0, 9000),
            Bound(0, 9000),
        ]
        components = self.create_components(2, capacity, soc, bounds)

        algorithm = DistributionAlgorithm(distributor_exponent=0.5)
        result = algorithm.distribute_power(1000, components)

        assert result.distribution == {1: 600, 3: 400}
        assert result.remaining_power == 0

        algorithm = DistributionAlgorithm(distributor_exponent=0)
        result = algorithm.distribute_power(1000, components)

        assert result.distribution == {1: 500, 3: 500}
        assert result.remaining_power == 0
