import warnings
from sys import exit

import numpy as np
from numpy.linalg import pinv
from pandas import DataFrame

import pydynpd.specification_tests as tests
from pydynpd.command import command
from pydynpd.common_functions import Windmeijer
from pydynpd.dynamic_panel_model import dynamic_panel_model
from pydynpd.info import step_result
from pydynpd.model_organizer import model_oranizer
from pydynpd.model_summary import model_summary
from pydynpd.panel_data import panel_data

warnings.filterwarnings("ignore", category=RuntimeWarning)


class abond:

    def __init__(self, command_str, df: DataFrame, identifiers: list):

        if len(identifiers) != 2:
            print('two variables needed')
            exit()

        user_command = command(command_str, df.columns)
        pdata = panel_data(df, identifiers, user_command.variables, user_command.options)
        self.models = []
        if user_command.options.beginner:
            m_manager = model_oranizer(user_command, pdata)
            num_models = len(m_manager.models.list_variables)
            j=0
            for i in range(num_models):
                variables = m_manager.models.list_variables[i]
                com_str = m_manager.models.list_command_str[i]
                try:
                    model = dynamic_panel_model(pdata, variables, user_command.options, com_str, user_command.part_2, user_command.part_3)
                    print('model ' + str(j + 1))
                    print(model.command_str)
                    self.regular_process(model, True)
                    j+=1
                except Exception as e:

                    continue
        else:
            model = dynamic_panel_model(pdata, user_command.variables, user_command.options, command_str, user_command.part_2, user_command.part_3)
            self.regular_process(model)

    def regular_process(self, model: dynamic_panel_model, beginner_mode=False):

        model.step_results = []

        _XZ, _Zy = self.calculate_basic(model)
        _XZ_t = _XZ.transpose()
        _Zy_t = _Zy.transpose()

        self.GMM(model, _XZ, _XZ_t, _Zy, _Zy_t, 1)
        if model.options.steps == 1 or model.options.steps == 2:
            self.GMM(model, _XZ, _XZ_t, _Zy, _Zy_t, 2)
            self.perform_test(model, 2)
        else:
            self.iterative_GMM(model, _XZ, _XZ_t, _Zy, _Zy_t)
            self.perform_test(model, model.options.steps)

        to_print = True

        if beginner_mode:
            to_print = self.check_model(model)
        if to_print:
            model.form_regression_table()
            ms = model_summary()
            ms.print_summary(model)
            self.form_results(model)

    def iterative_GMM(self, model, _XZ, _XZ_t, _Zy, _Zy_t):
        current_step = 1
        converge = False
        while not converge:
            previous_step = current_step
            current_step += 1
            self.GMM(model, _XZ, _XZ_t, _Zy, _Zy_t, current_step)
            beta_current = model.step_results[current_step - 1].beta
            beta_previous = model.step_results[current_step - 2].beta
            for j in range(beta_current.shape[0]):
                temp = (beta_current[j] - beta_previous[j]) ** 2
                temp2 = (beta_previous[j]) ** 2
                if j == 0:
                    nom = temp
                    denom = temp2
                else:
                    nom += temp
                    denom += temp2
            crit = np.sqrt(nom / denom)

            if crit < 0.00001:
                converge = True
                model.options.steps = current_step

    def GMM(self, model: dynamic_panel_model, _XZ, _XZ_t, _Zy, _Zy_t, step: int):
        N = model.N
        num_obs = model.num_obs
        z_list = model.z_list
        _z_t_list = model._z_t_list
        Cx_list = model.final_xy_tables['Cx'].dat
        Cy_list = model.final_xy_tables['Cy'].dat

        if step == 1:
            H1 = self.get_H1(model)
            W = self.calculate_W(H1, model)
            current_step = step_result(W)
            W_inv = current_step.W_inv
            model.step_results.append(current_step)

        if step >= 2:
            previous_step = model.step_results[step - 2]
            W = previous_step.W_next
            current_step = step_result(W)
            model.step_results.append(current_step)
            W_inv = current_step.W_inv

        _XZ_W = _XZ @ W_inv
        _M_inv = _XZ_W @ _XZ_t
        M = pinv(_M_inv)
        _M_XZ_W = M @ _XZ_W

        beta = _M_XZ_W @ _Zy_t
        residual = self.calculate_residual(Cy_list, Cx_list, beta)
        _residual_t = residual.transpose()
        SS = (_residual_t @ residual) * (1.0 / 2 / num_obs)

        z_height = int(z_list.shape[0] / N)
        r_height = int(residual.shape[0] / N)
        for i in range(N):
            z = z_list[(i * z_height):(i * z_height + z_height), :]
            u = residual[(i * r_height):(i * r_height + r_height), :]
            # u_t=_residual_t[:, (i*r_height):(i*r_height+r_height)]
            if i == 0:
                zs = z @ u
                ZuuZ = zs @ zs.transpose()
            else:
                temp_zs = z @ u
                zs += temp_zs
                ZuuZ += temp_zs @ temp_zs.transpose()

        W_next = ZuuZ * (1.0 / N)

        current_step._XZ_W = _XZ_W
        current_step.M = M
        current_step._M_XZ_W = _M_XZ_W
        current_step.beta = beta
        current_step.residual = residual
        current_step._residual_t = _residual_t
        current_step.SS = SS
        current_step.zs = zs

        current_step.ZuuZ = ZuuZ

        current_step.W_next = W_next

        current_step.vcov = self.vcov(model, step)
        current_step.std_err = np.sqrt(np.diag(current_step.vcov))

    def calculate_basic(self, model):

        z_list = model.z_list
        _z_t_list = model._z_t_list
        Cx = model.final_xy_tables['Cx']
        Cy = model.final_xy_tables['Cy']
        Cx_list = Cx.dat
        Cy_list = Cy.dat

        z_height = int(z_list.shape[0] / model.N)
        x_height = Cx.unit_height

        for i in range(model.N):
            z = z_list[(z_height * i):(z_height * i + z_height), :]
            z_t = _z_t_list[:, (z_height * i):(z_height * i + z_height)]
            x = Cx_list[(x_height * i):(x_height * i + x_height), :]
            y = Cy_list[(x_height * i):(x_height * i + x_height), :]
            if i == 0:
                temp_xz = (z @ x).transpose()
                temp_zy = (z @ y).transpose()
            else:
                temp_xz += (z @ x).transpose()
                temp_zy += (z @ y).transpose()
        return (temp_xz, temp_zy)

    def calculate_W(self, H, model):
        # W1 = (1.0 / N) * sum_product2([z_list, H1, _z_t_list], [(N, 1), (1, 1), (1, N)])
        z_height = int(model.z_list.shape[0] / model.N)

        for i in range(model.N):
            z = model.z_list[(z_height * i):(z_height * i + z_height), :]
            z_t = model._z_t_list[:, (z_height * i):(z_height * i + z_height)]

            if i == 0:
                temp_W = z @ H @ z_t

            else:
                temp_W += z @ H @ z_t

        return temp_W

    def calculate_residual(self, y_list, x_list, beta):

        tbr = y_list - x_list @ beta

        return (tbr)

    def vcov(self, model: dynamic_panel_model, step: int):
        # report robust vcov only

        z_list = model.z_list
        Cx = model.final_xy_tables['Cx']

        if step >= 2:
            the_step = model.step_results[step - 1]
            previous_step = model.step_results[step - 2]
            M2 = the_step.M
            _M2_XZ_W2 = the_step._M_XZ_W
            _W2_inv = the_step.W_inv
            zs2 = the_step.zs
            vcov_step_previous = previous_step.vcov
            residual1 = previous_step.residual
            return Windmeijer(M2, _M2_XZ_W2, _W2_inv, zs2,
                              vcov_step_previous, Cx, z_list, residual1, model.N)
        elif step == 1:
            step_1 = model.step_results[0]
            _M_XZ_W = step_1._M_XZ_W
            W2 = step_1.W_next
            return model.N * (_M_XZ_W @ W2 @ _M_XZ_W.transpose())

    def perform_test(self, model, step):
        step1 = model.step_results[0]
        step2 = model.step_results[1]
        num_instru = model.z_information.num_instr
        Cx = model.final_xy_tables['Cx']

        step = model.options.steps
        if step == 1 or step == 2:
            _W2_inv = step2.W_inv
            zs = step2.zs

            model.hansen = tests.hansen_overid(_W2_inv, model.N, zs, num_instru, \
                                               Cx.width)
        else:
            current_step = model.step_results[step - 1]
            _W2_inv = current_step.W_inv
            zs = current_step.zs
            model.hansen = tests.hansen_overid(_W2_inv, model.N, zs, num_instru, \
                                               Cx.width)

        try:
            model.AR_list = tests.AR_test(model, step, 2)
        except Exception as e:
            raise Exception(e)

    def get_H1(self, model: dynamic_panel_model):
        z_list = model.z_list
        z_inf = model.z_information
        width = z_list.shape[1]

        tbr = np.zeros((width, width), dtype='float64')
        i, j = np.indices(tbr.shape)
        tbr[np.logical_and(i == j, i < z_inf.diff_width)] = 2
        tbr[np.logical_and(i == j - 1, j < z_inf.diff_width)] = -1
        tbr[np.logical_and(j == i - 1, i < z_inf.diff_width)] = -1

        tbr[np.logical_and(i == j, i >= z_inf.diff_width)] = 1

        tbr[np.logical_and(i == j + z_inf.diff_width, j < z_inf.diff_width)] = -1
        tbr[np.logical_and(i == 1 + j + z_inf.diff_width, j < z_inf.diff_width)] = 1
        tbr[np.logical_and(j == i + z_inf.diff_width, i < z_inf.diff_width)] = -1
        tbr[np.logical_and(j == 1 + i + z_inf.diff_width, i < z_inf.diff_width)] = 1

        return (tbr)

    def form_results(self, model):
        step = len(model.step_results)
        the_list = model.step_results[step - 1]

        self.models.append(model)  # results = {}

    def check_model(self, model):
        tbr = False
        num_ARs = len(model.AR_list)
        last_AR = model.AR_list[num_ARs - 1]

        if last_AR.P_value > 0.05:
            if model.hansen.p_value > 0.05 and model.hansen.p_value < 0.99999:
                return True
        else:
            return False
