# Copyright © 2017 Ondrej Martinsky, All rights reserved
# http://github.com/omartinsky/pybor
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


from instruments.deposit import *
from instruments.future import *
from instruments.basisswap import *
from instruments.crosscurrencyswap import *
from instruments.swap import *
from instruments.termdeposit import *
import numpy
from collections import OrderedDict

from pandas import *
import scipy.optimize
import copy, os


def coalesce(*arg):
    for el in arg:
        if el is not None:
            return el
    return None


class CurveTemplate:
    def __init__(self, curve_name):
        self.curve_name = curve_name
        self.instruments = []


class ProgressMonitor:
    def __init__(self):
        self.counter = 0

    def reset(self):
        self.counter = 0

    def update(self):
        self.counter += 1
        if self.counter % 100 == 0:
            print('%i' % self.counter, end='', flush=True)
        elif self.counter % 10 == 0:
            print('.', end='', flush=True)


class BuildOutput:
    def __init__(self, input_prices, output_curvemap, jacobian_dIdP, instruments):
        self.input_prices = input_prices
        self.output_curvemap = output_curvemap
        self.jacobian_dIdP = jacobian_dIdP
        self.instruments = instruments

class PriceLadder(collections.OrderedDict):
    def create(data):
        if isinstance(data, pandas.DataFrame):
            od = collections.OrderedDict(data['Price'])
            return PriceLadder(od)
        elif isinstance(data, dict):
            return PriceLadder(data)
        else:
            raise BaseException("Unknown data type %s" % type(data))

    def instrument_list(self):
        return list(self.keys())

    def sublist(self, instrument_regex):
        l = []
        for k, v in self.items():
            if re.match(instrument_regex, k):
                l.append((k,v))
        return PriceLadder.create(OrderedDict(l))

    def dataframe(self):
        df = DataFrame.from_dict(self, orient='index')
        df.columns=['Price']
        return df


def calc_residual(curvemap, instrument_prices, instrument):
    r_actual = instrument.calc_par_rate(curvemap)
    price = instrument_prices[instrument.name_]
    r_target = instrument.par_rate_from_price(price)
    return r_actual - r_target

def calc_residuals(dofs, curve_builder, curvemap, instrument_prices):
    if curve_builder.progress_monitor:
        curve_builder.progress_monitor.update()
    assert not numpy.isnan(dofs).any()
    curvemap.set_all_dofs(dofs)

    all_instruments = curve_builder.all_instruments

    y = [calc_residual(curvemap, instrument_prices, i) for i in all_instruments]
    return y

class CurveBuilder:
    def __init__(self, excel_file, eval_date, progress_monitor=None):
        assert os.path.exists(excel_file)
        xl = ExcelFile(excel_file)
        self.df_instruments = xl.parse('Instrument Properties', index_col='Name', parse_cols='A:L').dropna()
        self.df_curves = xl.parse('Curve Properties', index_col='Curve', parse_cols='A:B').dropna()
        if (len(self.df_curves) == 0):
            raise BaseException("No curves found in spreadsheet")
        self.curve_templates = list()
        self.progress_monitor = progress_monitor
        self.eval_date = eval_date

        self.all_instruments = list()
        self.instrument_positions = dict()

        for curve_name in list(self.df_curves.index):  # Order of curves determined by XLS file:
            curve_template = CurveTemplate(curve_name)

            curve_df = self.df_instruments[
                self.df_instruments['Curve'] == curve_name]  # Order of instruments determined by XLS file
            for name, row in curve_df.iterrows():
                try:
                    instrument_type = row['Type']
                    fcastL = row['Forecast Curve Left']
                    fcastR = row['Forecast Curve Right']
                    discL = row['Discount Curve Left']
                    discR = row['Discount Curve Right']
                    convL = row['Convention Left']
                    convR = row['Convention Right']
                    start = row['Start']
                    length = row['Length']
                    enabled = row['Enabled']
                    assert enabled in 'YN'
                    if enabled == 'N':
                        continue

                    if instrument_type == 'Deposit':
                        assert (discL == "na")
                        assert (discR == "na")
                        assert (fcastL != 'na')
                        assert (fcastR == 'na')
                        inst = Deposit(name,
                                       curve_forecast=fcastL,
                                       start=create_date(start, eval_date),
                                       len=Tenor(length),
                                       convention=conventions[convL])
                    elif instrument_type == 'Future':
                        assert (discL == "na")
                        assert (discR == "na")
                        assert (fcastL != 'na')
                        assert (fcastR == 'na')
                        inst = Future(name,
                                      curve_forecast=fcastL,
                                      start=create_date(start, eval_date),
                                      len=Tenor(length),
                                      convention=conventions[convL])
                    elif instrument_type == 'Swap':
                        assert (discL != "na")
                        assert (discR == "na")
                        assert (fcastL != 'na')
                        assert (fcastR == 'na')
                        inst = Swap(name,
                                    curve_forecast=fcastL,
                                    curve_discount=discL,
                                    start=create_date(start, eval_date),
                                    length=Tenor(length),
                                    convention_fixed=conventions[convL],
                                    convention_float=conventions[convR])
                    elif instrument_type == 'BasisSwap':
                        assert (discL != "na")
                        assert (discR == "na")
                        assert (fcastL != 'na')
                        assert (fcastR != 'na')
                        inst = BasisSwap(name,
                                         curve_forecast_l=fcastL,
                                         curve_forecast_r=fcastR,
                                         curve_discount=discL,
                                         start=create_date(start, eval_date),
                                         length=Tenor(length),
                                         convention_l=conventions[convL],
                                         convention_r=conventions[convR])
                    elif instrument_type == 'CrossCurrencySwap':
                        assert (discL != "na")
                        assert (discR != "na")
                        assert (fcastL == 'na') != (fcastR == 'na')
                        inst = CrossCurrencySwap(name,
                                                 curve_discount_l=discL if fcastR != "na" else discR,
                                                 curve_discount_r=discR if fcastR != "na" else discL,
                                                 curve_forecast_r=fcastR if fcastR != "na" else fcastL,
                                                 start=create_date(start, eval_date),
                                                 length=Tenor(length),
                                                 convention_l=conventions[convL],
                                                 convention_r=conventions[convR])
                    elif instrument_type == 'TermDeposit':
                        assert (discL != "na")
                        assert (discR == "na")
                        assert (fcastL != 'na')
                        assert (fcastR == 'na')
                        inst = TermDeposit(name,
                                           curve_forecast=fcastL,
                                           curve_discount=discL,
                                           start=create_date(start, eval_date),
                                           length=Tenor(length),
                                           convention=conventions[convL])
                    else:
                        raise BaseException("Unknown instrument type %s" % instrument_type)
                except BaseException as ex:
                    raise BaseException("Error processing instrument %s" % name) from ex

                self.instrument_positions[inst.get_name()] = len(self.all_instruments)
                self.all_instruments.append(inst)
                curve_template.instruments.append(inst)

            if len(curve_template.instruments) == 0:
                raise BaseException("No instruments found for curve template %s" % curve_template.curve_name)

            self.curve_templates.append(curve_template)
        pass


    def get_curve_names(self):
        return [t.curve_name for t in self.curve_templates]


    def reprice(self, curvemap):
        out = OrderedDict()
        for curve_template in self.curve_templates:
            for instrument in curve_template.instruments:
                rate = instrument.calc_par_rate(curvemap)
                out[instrument.name_] = instrument.price_from_par_rate(rate)
        return PriceLadder(out)

    def get_instrument_rates(self, price_ladder):
        maturities = [self.get_instrument_by_name(name).get_pillar_date() for name in price_ladder.keys()]
        rates = [self.get_instrument_by_name(name).par_rate_from_price(price) for name, price in price_ladder.items()]
        return array(maturities), array(rates)

    def parse_instrument_prices(self, prices):
        if isinstance(prices, dict):
            return prices
        elif isinstance(prices, pandas.DataFrame):
            try:
                return dict(zip(prices['Instrument'], prices['Price']))
            except BaseException as ex:
                raise BaseException("Unable to parse dataframe with instrument prices") from ex
        else:
            raise BaseException("Unknown type")


    def build_curves(self, instrument_prices):
        instrument_prices = self.parse_instrument_prices(instrument_prices)

        curvemap = CurveMap()

        # Create unoptimized curve map
        pillar_count = 0
        for curve_template in self.curve_templates:
            pillar = []
            for instrument in curve_template.instruments:
                pillar_date = instrument.get_pillar_date()
                pillar.append(pillar_date)
            pillar = array(sorted(set(pillar)))
            assert len(pillar) > 0, "Pillars are empty"
            dfs = exp(-0.02 * (pillar - self.eval_date) / 365.)  # initial rates will be circa 2%
            curve_name = curve_template.curve_name
            interpolation = enum_from_string(InterpolationMode, self.df_curves.loc[curve_name].Interpolation)
            print("Creating pillars %i - %i for curve %s" % (pillar_count, pillar_count + len(pillar), curve_name))
            pillar_count += len(pillar)
            curve = Curve(curve_name, self.eval_date, pillar, dfs, interpolation)
            curvemap[curve_name] = curve

        dofs = curvemap.get_all_dofs()

        if (self.progress_monitor):
            self.progress_monitor.reset()

        arguments = (self, curvemap, instrument_prices)

        #solution = scipy.optimize.root(fun=calc_residuals, x0=dofs, args=arguments)
        bounds = (zeros(len(dofs)), ones(len(dofs)))
        solution = scipy.optimize.least_squares(fun=calc_residuals, x0=dofs, args=arguments, bounds=bounds)

        assert isinstance(solution, scipy.optimize.OptimizeResult)

        if not solution.success:
            raise BaseException(solution.message)
        curvemap.set_all_dofs(solution.x)

        bump_size = 1e-8
        e0 = array(calc_residuals(solution.x, *arguments))
        jacobian_dIdP = []
        for i in range(len(solution.x)):
            bump_vector = zeros(len(solution.x))
            bump_vector[i] += bump_size
            e = array(calc_residuals(solution.x + bump_vector, *arguments))
            jacobian_dIdP.append((e - e0) / bump_size)
        # this jacobian_dIdP contains dI/dP.  Rows=Pillars  Cols=Instruments
        # after inversion, it will contain dP/dI.   Rows=Instruments   Cols=Pillars
        jacobian_dIdP = matrix(jacobian_dIdP)

        return BuildOutput(instrument_prices, curvemap, jacobian_dIdP, self.all_instruments)

    def get_instrument_by_name(self, name):
        pos = self.instrument_positions[name]
        return self.all_instruments[pos]

    def getframe(self):
        cp = copy.deepcopy(self.df_instruments)
        cp['start2'] = None
        cp['end2'] = None

        for inst in self.all_instruments:
            assert isinstance(inst, Instrument)
            cp['start2'][inst.name_] = inst.get_start_date()
            cp['end2'][inst.name_] = inst.get_pillar_date()
        return cp
