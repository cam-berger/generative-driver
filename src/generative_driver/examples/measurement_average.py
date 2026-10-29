"""Bounded averaging of a declared driver measurement; no calibration claim."""
import argparse

from generative_driver.sdk import Application, ManagedDriver, ToolSpec


def average_application(driver, operation='measure', field='temperature'):
    output = driver.contract['operations'][operation]['outputs'][field]
    application = Application('measurement-average', drivers={'sensor': driver})
    spec = ToolSpec(name='sensor_average',
        description='Take bounded measurements and return their arithmetic mean in the source unit. This makes no new calibration claim.',
        parameters={'count': {'schema': {'type': 'integer', 'minimum': 1, 'maximum': 16},
            'description': 'Number of fresh measurements.', 'unit': None, 'uncertainties': []}},
        outputs={
            'mean': {'schema': {'type': 'number'}, 'description': 'Arithmetic mean.',
                     'unit': output['unit'], 'uncertainties': output['uncertainties']},
            'count': {'schema': {'type': 'integer'}, 'description': 'Completed measurement count.',
                      'unit': None, 'uncertainties': []},
            'unit': {'schema': {'type': ['string', 'null']}, 'description': 'Source unit; null means unknown.',
                     'unit': None, 'uncertainties': []}},
        dependencies={'sensor': [operation]})

    def average(context, arguments):
        values = [context.invoke('sensor', operation)['outputs'][field] for _ in range(arguments['count'])]
        return {'count': len(values), 'mean': sum(values)/len(values), 'unit': output['unit']}

    application.register(spec, average)
    return application


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True)
    parser.add_argument('--home')
    parser.add_argument('--session', required=True)
    parser.add_argument('--operation', default='measure')
    parser.add_argument('--field', default='temperature')
    args = parser.parse_args()
    average_application(ManagedDriver(args.package, args.session, home=args.home),
                        args.operation, args.field).serve()


if __name__ == '__main__':
    main()
